CREATE TABLE IF NOT EXISTS batches (
    batch_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    folder_name TEXT NOT NULL,
    destination_type TEXT,
    destination_name TEXT,
    confidence  REAL NOT NULL DEFAULT 0.0,
    status      TEXT NOT NULL DEFAULT 'ROUTING',
    file_count  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);

ALTER TABLE documents ADD COLUMN batch_id INTEGER REFERENCES batches(batch_id);
