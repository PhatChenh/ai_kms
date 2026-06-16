-- Migration 014: Self-learning tables for Phase 10
-- fact_corrections: records every correction operation with metadata
CREATE TABLE IF NOT EXISTS fact_corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES knowledge_entries(id) ON DELETE CASCADE,
    operation TEXT NOT NULL,
    reason_category TEXT,
    feedback TEXT,
    old_fact TEXT,
    new_fact TEXT,
    old_trust_score REAL,
    new_trust_score REAL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_fc_entry ON fact_corrections(entry_id);
CREATE INDEX IF NOT EXISTS idx_fc_reason ON fact_corrections(reason_category);

-- reports: stores on-demand synthesized reports
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_type TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    prompt_used TEXT,
    filters_used TEXT,
    sources_used TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_reports_type ON reports(report_type, created_at DESC);

-- entry_comments: additive annotations on knowledge entries
CREATE TABLE IF NOT EXISTS entry_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES knowledge_entries(id) ON DELETE CASCADE,
    comment_text TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ec_entry ON entry_comments(entry_id);

UPDATE schema_version SET version = 14;
