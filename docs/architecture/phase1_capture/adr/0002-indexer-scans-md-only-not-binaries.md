# Indexer scans only .md files, not binary attachments

`scan_vault()` skips any file that is not `.md` (case-insensitive). Binary files in the vault (PDFs, images, etc.) are not indexed.

**Status:** accepted

**Consequences**

- Binaries enter the system as INPUT via handlers (PDFHandler, etc.), get captured, summarised, and written as `.md`. The original attachment is not indexed — its derived note is.
- If a future phase needs to track attachments for de-duplication, that requires a separate `attachments` table and a different indexer — not an extension of the markdown indexer.
- Attachment moves are handled separately by `vault/writer.move_attachment()`, which takes no `updated_by_human` gate and returns `Result[Path]` (not `WriteOutcome`).
