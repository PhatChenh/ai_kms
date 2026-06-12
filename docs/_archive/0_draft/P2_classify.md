### TD-040 · Single-file capture never sets batch_id — batch association only possible via folder drop
**What:** `capture_file()` is called with `ctx.batch_id = None` in all single-file code paths (CLI, scan, watcher on_create). `_insert_batch()` is only called inside `capture_folder()`, which is only triggered when the watcher detects a stable folder drop. There is no mechanism for single-file capture to associate itself with a batch — not by destination folder, not by time window, not by user intent.

### TD-041 · scan_capture does not classify or batch-capture inbox subfolders
`scan_capture()` (`src/pipelines/capture.py`) does not detect or dispatch subfolder drops. `capture_folder()` is triggered only by `vault/watcher.py` on a stable folder-creation event, not by any CLI scan path. The desired behavior: `kms capture --scan` should detect unprocessed subfolders in `inbox/`, classify them via LLM, create a batch row, and assign a `batch_id` to all files inside — mirroring what `capture_folder()` does for watcher-triggered drops.

### TD-042 · reconcile_stale_tags (Stage 5) does not mark dirty for deprecated frontmatter keys
`reconcile_stale_tags` (`src/pipelines/reconcile.py`, Stage 5) sets `dirty=True` only for stale/missing domain tags and wrong `project:` field. It does not check `note.metadata.extra` for keys in `_DEPRECATED_KEYS`. A note that has `domain: finance` in frontmatter but already has a valid `domain/Finance` tag and correct `project:` field will never be written by Stage 5 — so `dumps()` never gets to strip the deprecated key. Fix: add `if any(k in note.metadata.extra for k in _DEPRECATED_KEYS): dirty = True` to Stage 5 before the `if not dirty: continue` guard (line ~359 in reconcile.py). The `model_copy` call at line 362 already preserves `extra`, so `dumps()` will strip it on the next write automatically.

### From Roadmap

#### Goal

After Phase 1, the system can read a note and write a summary. But the note stays in the inbox forever — nothing moves it. The goal of Phase 2 is to close that gap: the system should look at what a note is about and automatically file it into the right project or topic folder, with no effort from the user. The only time the user needs to act is when the AI genuinely is not sure — everything else should happen on its own.

---

#### How the pieces fit together

Phase 2 starts where Phase 1 left off. By this point, a note in the inbox already has a summary and tags written by the system. Phase 2 takes it from there.

```
# Phase 2 — Classify + Route: What Happens Inside
Scope: Shows what the system does when it classifies one note.
       Does NOT cover how the Project Registry is built or batch mode.

How to read this:
  Boxes      = steps the system takes, in order
  BOLD NAME  = the component name — maps directly to the Components section below
  Arrows     = what flows to the next step
  Fork       = a decision with different outcomes

              Note in inbox
        (has summary + tags already
            written in Phase 1)
                    │
                    ▼
       ┌─────────────────────────┐      ┌──────────────────────┐
       │ CLASSIFY                │─────►│ PROJECT REGISTRY     │
       │ AI reads the note and   │◄─────│ List of all valid    │
       │ picks a destination     │      │ projects and folders │
       │ folder                  │      └──────────────────────┘
       └─────────────┬───────────┘
                     │
                     ▼
       ┌─────────────────────────┐
       │ CONFIDENCE GATE         │
       │ Checks how sure the AI  │
       │ is about its choice     │
       └─────────────┬───────────┘
                     │
          ┌──────────┼──────────────┐
          │          │              │
         HIGH      MEDIUM          LOW
          │          │              │
          ▼          ▼              ▼
   ┌───────────┐ ┌──────────────┐ ┌──────────────┐
   │ MOVE      │ │ Flag note —  │ │ Leave in     │
   │ Moves     │ │ wait for     │ │ inbox, mark  │
   │ note to   │ │ user to      │ │ "needs       │
   │ folder    │ │ confirm      │ │ review"      │
   └─────┬─────┘ └──────┬───────┘ └──────┬───────┘
         │               │                │
         └───────────────┼────────────────┘
                         │
                         ▼
            ┌─────────────────────────┐
            │ DECISION LOG            │
            │ Records: folder chosen, │
            │ confidence, reasoning   │
            └─────────────────────────┘

Note: MEDIUM and LOW outcomes are not separate components — they are the
      two "stop" results produced by the Confidence Gate.
Simplified: Route and Move are combined into one box to keep the diagram readable.
```

---

#### Components

---

**Project Registry** *(new — build this first, others depend on it)*

Build a lookup table that stores all active projects and which domain each one belongs to.

- **Input:** The vault folder structure (scan `Projects/` and `Domain/` folders at startup)
- **Output:** A list of valid destinations — project names, domain names, and their relationships — that other steps can query
- **Rules:**
  - Must update itself when new project folders are added; should not require manual maintenance
  - If a project has no domain mapping, default to `Uncategorized`
- **Reuse:** `vault/paths.py` for reading folder structure; `core/config.py` for vault root path

---

**Classification Prompt** *(new)*

Write the instructions that tell the AI how to classify a note.

- **Input:** Nothing at build time — this is a text file, not code
- **Output:** A YAML file at `prompts/classify.yaml` containing the prompt template, with placeholders for the note summary, tags, and the list of valid destinations
- **Rules:**
  - Must be a YAML file — never write the prompt directly in code
  - The prompt must instruct the AI to return: a destination folder name, a confidence score between 0 and 1, and a one-sentence reason for its choice
- **Reuse:** Look at existing prompts in `prompts/` folder for the expected YAML format

---

**Classify**

Build the AI step that reads a captured note and picks a destination folder.

- **Input:** A note's summary and tags (already written by Phase 1) + the list of valid destinations from the Project Registry
- **Output:** A destination folder name + a confidence score (0–1) + a one-sentence reasoning string
- **Rules:**
  - Must load the prompt from `prompts/classify.yaml` — never write the prompt as a string in code
  - Must call the AI through `llm/provider.py` — never call the AI library directly
  - Must return a `Success` or `Failure` result — never raise an exception or return `None`
- **Reuse:** `llm/provider.py` to call the AI; `llm/prompt_loader.py` to load the prompt; `core/result.py` for the return type

---

**Confidence Gate**

Build the step that decides what to do with the AI's answer based on how confident it is.

- **Input:** The confidence score (0–1) from Classify
- **Output:** One of three actions: `auto-move`, `flag-for-review`, or `mark-clueless`
- **Rules:**
  - The numeric thresholds that separate high / medium / low confidence must be read from `config/thresholds.yaml` — never hardcode a number like `0.85` in the code
  - `auto-move` → pass the note to Route; `flag-for-review` and `mark-clueless` → skip Route and Move, go straight to Decision Log
- **Reuse:** `core/config.py` to read thresholds

---

**Route**

Build the step that translates a destination name into an exact file path.

- **Input:** A destination folder name (e.g. "Project Alpha" or "Domain / Finance")
- **Output:** The full path where the note should be placed
- **Rules:**
  - Must use `vault/paths.py` helpers to construct paths — never build paths by string concatenation
  - Must verify the destination folder exists before returning a path; return a `Failure` if it does not
- **Reuse:** `vault/paths.py` for `project_attachment()`, `domain_attachment()`, and related helpers

---

**Move**

Build the step that physically moves the note from its current location to the destination.

- **Input:** The note's current path + the destination path from Route
- **Output:** The note at its new location; a `Success` or `Failure` result
- **Rules:**
  - Must go through `vault/writer.py` — never move files directly with filesystem calls
  - If the note has `updated_by_human = true` in its metadata, do not move it — return a `Failure` with a conflict message instead
- **Reuse:** `vault/writer.py` has `move_note(src, dst)` — use that directly

---

**Decision Log**

Build the step that records the outcome of every classification, regardless of what happened.

- **Input:** The note path, the chosen destination, the confidence score, the reasoning string, and the final action taken (`auto-move`, `flag-for-review`, or `mark-clueless`)
- **Output:** A new entry written to the audit log
- **Rules:**
  - Must run for every note — including ones that were flagged or left in inbox, not just ones that were moved
  - Must write through `core/audit.py` — never write to the log directly
  - Entry must include: timestamp, note path, destination, confidence score, reasoning, and action taken
- **Reuse:** `core/audit.py` `write()` function; `storage/audit_log.py` for the underlying storage

---

#### What the user sees

- Run one command on a single note → note gets classified and filed (or flagged)
- Run one command on the whole inbox → every note gets processed at once
- Notes that need human review stay in inbox with a visible flag
