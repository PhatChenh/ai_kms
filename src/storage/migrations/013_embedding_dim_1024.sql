-- Migration 013: Resize embedding vectors from 384 to 1024 dimensions.
-- Supports greennode/greennode-embedding-large-1007 via OpenAI-compat API.
-- Safe to run on fresh DBs (007/012 already create FLOAT[1024]).
-- On existing DBs: drops old 384-dim tables and recreates at 1024.
-- No data migration — project has not shipped yet.

DROP TABLE IF EXISTS embeddings_vec;
CREATE VIRTUAL TABLE IF NOT EXISTS embeddings_vec USING vec0(
    vault_path  TEXT PRIMARY KEY,
    embedding   FLOAT[1024]
);

DROP TABLE IF EXISTS facts_vec;
CREATE VIRTUAL TABLE IF NOT EXISTS facts_vec USING vec0(
    entry_id INTEGER PRIMARY KEY,
    embedding FLOAT[1024]
);

UPDATE schema_version SET version = 13;
