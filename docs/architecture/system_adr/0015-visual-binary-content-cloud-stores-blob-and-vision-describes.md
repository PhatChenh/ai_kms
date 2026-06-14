# Visual / binary content — the cloud stores the blob in object storage and vision-describes it; the daemon uploads raw bytes

_Created: 2026-06-13_

Surfaced during the Phase 6 (Daemon) grill. The system must handle files whose meaning is **visual, not textual** — photos, scanned docs, and especially **informative graphs/charts** a user drops into the vault. Text extraction captures nothing useful for these. **Decision: the daemon uploads the raw bytes of any file it cannot extract text from (it stays AI-free); the cloud persists the blob in VNG Object Storage (already in the stack for Litestream) and runs a vision model once to produce a searchable text description; retrieval finds the file by that description from anywhere, returns the local path when the laptop is open, and serves the stored blob to phone/web when it is closed.** This amends the three-tier retrieval model (rearch §8/§9): for stored blobs, Tier 3 is no longer strictly laptop-dependent.

**Status:** accepted (direction). Implementation spans phases — see "Phasing" below.

## Context

- **The daemon is AI-free** (hard constraint). It cannot describe a graph. All visual understanding — OCR *or* a vision model — must happen cloud-side.
- **The current `/api/upload` requires `extracted_text`** (returns 400 without it). There is no path to send an image today. The rearch doc's "extraction fails → upload raw bytes" fallback was never built. This ADR closes that gap.
- **The three-tier model** (rearch §8) made Tier 3 (raw file) "laptop-dependent — unavailable when laptop closed" (§9). That makes a dropped graph invisible to the user on phone/web — unacceptable for "give me the graph about X."
- **VNG Object Storage is already a dependency** (Litestream SQLite backups, agentbase_research §11.2). Storing image blobs there reuses existing infra rather than adding a new one — which is what makes option (2) cheap.

## Decision — the layered design

1. **Daemon (Phase 6, slice A1) — upload bytes, no AI.** When a file has no usable text extraction (image, graph, unknown binary), the daemon uploads the **raw bytes + metadata** (mime type, raw-byte hash, size, vault_path) instead of failing. The cloud `/api/upload` is extended with a binary path to accept this. The daemon does **not** OCR or describe — that stays cloud-side (keeps the daemon thin and the PyInstaller bundle small).
2. **Cloud store (Phase 7 + deployment).** Persist the blob in VNG Object Storage, keyed by content hash / vault_path. SQLite (`documents`) holds a **reference** to the blob + metadata, not the bytes. (Exact storage shape — reference column vs new table, key scheme — decided at the Phase 7 design step.)
3. **Cloud understand (Phase 7).** The summarizer is extended with a vision model: for an image/graph it produces a searchable **text description** ("bar chart of Q2 revenue by region; Finance +20%…") stored as the document's summary / `full_body`. This is what makes the visual findable by `kms_search`.
4. **Retrieval (Phase 9).** `kms_search` finds the file by its description from any device. `kms_inspect` Tier 3 returns the **local vault path when the laptop is open**, and **serves the stored blob from object storage when it is closed** — amending the "Tier 3 = laptop-dependent" rule for files whose blob was kept.

## Considered options

- **(1) Image stays local; cloud stores only the description.** Cheapest, matches the original three-tier model. Rejected as the target: the user cannot *see* a dropped graph from phone/web — only read its description. Also, discarding the bytes means the cloud cannot re-describe later (e.g., better vision model) without the laptop.
- **(2) Cloud keeps the blob + vision-describes (chosen).** Reuses the object store already present; one mechanism delivers both search-by-description and remote viewing; bytes retained for re-processing.
- **(3) Daemon OCRs locally.** Rejected: pushes AI/heavy models (tesseract + language data, or a vision model) into the thin daemon, bloats the installer, and OCR alone can't interpret a graph. Visual understanding belongs cloud-side where the vision model already runs.

## Consequences

- **Amends the three-tier model** (rearch §8) and the graceful-degradation table (rearch §9): for files with a stored blob, "raw file when laptop closed" becomes *available* (served from object storage), not unavailable.
- **Privacy / data-residency shift.** User images now leave the laptop and live in cloud object storage — a change from "raw files stay local, only text goes to the cloud." Acceptable for this single-tenant personal deployment, but it must be a conscious, stated property (and a future multi-tenant or sensitive-vault scenario may need per-vault opt-out).
- **Cost & bandwidth.** Blobs are larger than text; uploads are slower on home internet and object storage costs more than text rows. Bounded for a personal vault; revisit if it grows.
- **Phase 6 commitment is small and concrete:** A1's uploader can send binary bytes, and the cloud upload endpoint accepts them. Nothing visual-*understanding* is built in Phase 6.
- **Deferred to owning phases (tracked here, not built now):** blob storage wiring + schema + vision-describe → **Phase 7**; blob-serving retrieval + `kms_inspect` rework → **Phase 9**. The Phase 7 design step decides the blob storage shape and key scheme.
- **Distinct from text capture:** text files keep going through the normal extract→summarize→store path unchanged. This ADR only governs files that fail text extraction.
