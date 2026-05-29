# documents.vault_path for attachment siblings = sibling .md path; NoteMetadata.attachment_path points to binary

`documents.vault_path` for a sibling row = `"Projects/<A>/attachment/.summaries/report.md"` (the indexed `.md` file). `NoteMetadata.attachment_path: str | None` frontmatter field carries the vault-relative path to the binary (`"Projects/<A>/attachment/report.pdf"`).

**Status:** accepted (OQ-AL1 Option C — hybrid)

**Considered Options**

- (A) Sibling-only (no pointer) — search hit opens summary; attachment rename breaks link silently.
- (B) Attachment-only — requires weakening the "indexer scans .md only" decision; highest cost.
- (C) Hybrid — chosen. Survives binary rename if sync updates frontmatter (Brief #3).

**Consequences**

- Option B conflicts with the indexer-scans-md-only decision (`vault_path` pointing at `.pdf` is incoherent).
- Every search hit resolves `documents.vault_path` to the sibling `.md`. To open the actual binary, consumers read `metadata.attachment_path` from sibling frontmatter.
- Phase 3 embeddings computed from sibling body (coherent — body is the AI-generated summary of the binary).
- Brief #3 sync must update `attachment_path` in frontmatter when binary is renamed/moved.
