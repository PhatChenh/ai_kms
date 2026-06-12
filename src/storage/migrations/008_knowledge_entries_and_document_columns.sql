-- 008: knowledge_entries table + document expansion columns
-- Adds the knowledge_entries table for structured fact storage
-- Adds 3 optional columns to documents (full_body, original_filename, file_size_bytes)

CREATE TABLE IF NOT EXISTS knowledge_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dimension TEXT,
    entity TEXT,
    tag TEXT,
    fact TEXT,
    status TEXT,
    confidence REAL,
    sources TEXT,
    reasoning TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

ALTER TABLE documents ADD COLUMN full_body TEXT;
ALTER TABLE documents ADD COLUMN original_filename TEXT;
ALTER TABLE documents ADD COLUMN file_size_bytes INTEGER;
