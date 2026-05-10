CREATE TABLE IF NOT EXISTS documents (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    vault_path       TEXT NOT NULL UNIQUE,
    title            TEXT NOT NULL,
    summary          TEXT,
    note_type        TEXT,
    confidence       REAL,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_by_human INTEGER NOT NULL DEFAULT 0,
    content_hash     TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp      TEXT NOT NULL DEFAULT (datetime('now')),
    pipeline       TEXT NOT NULL,
    stage          TEXT NOT NULL,
    source_ids     TEXT NOT NULL,
    decision       TEXT NOT NULL,
    confidence     REAL NOT NULL,
    reasoning      TEXT NOT NULL,
    outcome        TEXT NOT NULL,
    correlation_id TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS audit_log_no_update
  BEFORE UPDATE ON audit_log
  BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
  BEFORE DELETE ON audit_log
  BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;

CREATE TABLE IF NOT EXISTS corrections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL DEFAULT (datetime('now')),
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    field       TEXT NOT NULL,
    ai_value    TEXT,
    human_value TEXT
);

CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
INSERT OR IGNORE INTO schema_version (version) VALUES (0);

CREATE INDEX IF NOT EXISTS idx_documents_note_type  ON documents(note_type);
CREATE INDEX IF NOT EXISTS idx_documents_updated_at ON documents(updated_at);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp       ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_correlation     ON audit_log(correlation_id);
CREATE INDEX IF NOT EXISTS idx_audit_pipeline_ts     ON audit_log(pipeline, timestamp);
