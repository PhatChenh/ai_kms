## Folder Processing in Capture Phase

**Goal:** When a user drops a folder into inbox (or creates one there), treat it as a single unit — classify, route, and move it together as a batch. Files and folder travel together. Structure is preserved.

**Key decisions made:**

**1. Folder metadata → SQLite, not a vault file.** A `batches` table records the folder as an entity: name, detected intent, file count, destination, confidence, timestamp. The vault only shows outputs (Briefing entries). Nothing hidden in the vault.

**2. Folder stays intact as a unit.** No tearing apart files to different destinations. The folder is classified as a whole, routed to one destination, all files move with it.

**3. Routing rules:**
- Dropped in `inbox/` → classify and route to either `Projects/<name>/materials/` or `Domain/<name>/notes/`
- Already in `Projects/` or `Domain/` → skip routing, process in place (summarize, tag, index)
- Confidence-gated same as single files: ≥0.85 auto-move, 0.60–0.85 flag for human, <0.60 stays in inbox

**4. The hard problem — detection.** A folder drop fires dozens of filesystem events simultaneously. You need to know when the drop is *complete* before processing. The industry pattern for this is called a **stability cool-off** (also called debounce + quiescence detection):
- Watch for new directory creation event
- Collect all child file events under that directory
- Start a timer that resets on every new event
- When no new events arrive for N seconds (e.g. 3s) → declare the drop complete and trigger the pipeline

IBM's Aspera watchfolder calls this "drop detection cool-off." The key insight: a watchfolder groups new or updated files it detects into "drops" defined by a snapshot creation period — all files in a given drop are transferred in the same transfer session, post-processed together, and reported as a unit. That's exactly your model.

The right batching strategy depends on which failure mode is more expensive: if a missed change is catastrophic (file sync, backup tool), use coalescing with per-file state tracking and stable-state detection. The implementation cost is higher, but the correctness guarantee is strongest. For your case, missing a file in a folder drop is bad — use coalescing, not simple debounce.

**5. Two-stage pipeline for folder drops:**

```
Stage 1 — Folder-level
  detect drop complete →
  extract folder name + file manifest →
  classify folder as a whole (what is this batch about?) →
  decide destination (confidence-gated) →
  write batch record to SQLite

Stage 2 — File-level (runs after Stage 1 resolves destination)
  for each file in folder:
    run standard capture pipeline (extract → summarize → tag)
    inherit folder's destination + batch_id as metadata context
    write to SQLite with batch_id foreign key
  
  move entire folder to destination
```

The folder classification in Stage 1 feeds into each file's classification as additional context — not as a routing override, but as a signal: "these files arrived together named X, probably about Y."

**Constraints:**
- Never process a partial drop — wait for stability cool-off
- Never split the folder — one destination per batch
- Folder name + sibling file list = context injected into each file's classification prompt
- `batch_id` is the FK linking all files back to the folder entity in SQLite
- Idempotency: if folder already at correct destination, skip move, still run Stage 2

**Open questions:**
- What's the right cool-off window? 3s is standard for local filesystem; might need tuning if user drops large folders over a slow network drive
- What happens if a folder drop partially fails (2 of 5 files error)? Does the whole batch stay in inbox, or do you move the successes and flag the failures?