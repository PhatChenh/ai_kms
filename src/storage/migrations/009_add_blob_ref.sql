-- 009: blob-reference columns for binary capture (Phase 7B)
-- Adds two nullable columns to documents so binary rows can point to object storage
-- and carry a MIME type for rendering / download decisions.

ALTER TABLE documents ADD COLUMN blob_ref TEXT;
ALTER TABLE documents ADD COLUMN mime_type TEXT;
