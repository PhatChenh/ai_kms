-- Search indexes for Phase 3 retrieval infrastructure.
-- embeddings_vec: sqlite-vec virtual table for semantic (KNN) search.
-- notes_fts:       FTS5 virtual table for keyword (BM25) search.

-- COUPLING: float[384] is coupled to sentence-transformers all-MiniLM-L6-v2.
--           If the embedding model changes, this DDL must be updated.

CREATE VIRTUAL TABLE IF NOT EXISTS embeddings_vec USING vec0(
    vault_path  TEXT PRIMARY KEY,
    embedding   FLOAT[384]
);

CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    vault_path UNINDEXED,
    title,
    summary,
    body,
    tokenize='porter unicode61'
);
