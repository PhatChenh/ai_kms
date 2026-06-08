# Unify the two classify prompts via one prompt fed a normalized subject block

The single-note classify prompt and the whole-folder classify prompt are merged into one prompt. Both callers first turn their input — a single note, or a folder of files — into the same short "subject" description, and the one prompt only ever sees that shape. The old folder-specific prompt is removed.

**Status:** proposed

**Context**

Two prompts exist today and they take different inputs:

- `prompts/classify.yaml` — variables `title`, `summary`, `tags`, `valid_destinations`. Used by the (built-but-unused) `classify()` pure function for single notes.
- `prompts/classify_folder.yaml` — variables `folder_name`, `file_manifest`, `vault_context`. Used by `capture_folder()` for whole-folder drops.

Both return the same JSON decision shape (`target_type`, `target_name`, `confidence`; the note prompt also returns `reasoning`). The routing rules they encode (project for active work, domain for durable knowledge, never return "Uncategorized", confidence guidance) are the same intent expressed twice. Phase 2 adds a third caller — the new inline classify step in single-file capture — which would otherwise reuse the note prompt and leave two prompts to maintain. The locked feature decision (Decision 5) requires unifying them, in two phases: Phase 1 ships the unified prompt for the single-file inline path; Phase 2 migrates the working folder path onto the same prompt while holding all 956 folder tests green.

The hard part: a note prompt reads a note's summary; a folder prompt reads a list of filenames. "One prompt" must handle both shapes.

**Decision**

Adopt one flat unified prompt with no internal branching. Each caller normalizes its own input into a shared `subject` text block before calling the AI:

- The note caller renders title / summary / tags into the `subject`.
- The folder caller renders folder name / file list into the same `subject`.

The unified `prompts/classify.yaml` takes `subject` + `valid_destinations` and returns the four fields including `reasoning`. `prompts/classify_folder.yaml` is deleted. The `classify()` engine becomes the single classification call for both note and folder callers.

**Considered options**

- **One prompt template with two render contexts (conditional body, `{% if %}` in the prompt)** — rejected. Keeps one file, but hides routing-irrelevant branching inside the prompt. A prompt with `if` branches is harder for a non-technical owner to read and tune (the project's stated default), and an accidentally-rendered empty section can confuse the model.
- **One prompt fed a normalized subject block (chosen)** — one flat, branch-free prompt. The cost is a small normalizer step in each caller, but the AI's input is identical regardless of source, and the prompt stays legible.
- **Keep two prompts forever** — rejected by locked Decision 5. Two prompts drift; a single routing-rule change must be made twice and tested twice.

**Consequences**

- Deleting `classify_folder.yaml` and changing the folder caller's prompt contract is hard to reverse — it touches the folder path that 956 tests cover. Phase 2 of the rollout must run those tests green before the folder caller is switched.
- A future reader will see a normalizer instead of two prompts and wonder why; this ADR is the answer.
- Classification quality for folders now depends on how well the folder `subject` preserves signal (folder name + file list). If quality dips, the fix is to enrich the folder normalizer, not to re-split the prompt.
- The P2-CL unit tests (P2-CL-01..06), which assert the note-shaped `classify()` signature, may change if `classify()` moves to a `subject`-shaped argument (Open Question OQ-CIC-3 in the design doc). That decision is finalized in the spec, not here.
