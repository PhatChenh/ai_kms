# Vietnamese filename normalization via Unicode NFC

Apply `unicodedata.normalize("NFC", ...)` to `vault_path` strings before storing in SQLite and before comparing paths from filesystem scans.

**Status:** accepted

**Consequences**

- macOS stores filenames in NFD (decomposed form); Python may read them as NFD strings. Vietnamese filenames use tonal diacritics with combining tone marks. Without NFC, the same filename produces two different Python strings depending on how it was read, causing the indexer to report spurious "deleted + added" for the same note.
- `vault/writer.py` applies NFC in `_to_vault_path()` before returning `vault_path` in `WriteOutcome`.
- `vault/indexer.py` applies NFC in `scan_vault()` when computing `VaultEntry.vault_path`.
