# MCP write-path uses kms_write and kms_move, not kms_capture and kms_classify

Phase 4 MCP exposes `kms_write` (create notes with user-directed metadata) and `kms_move` (relocate notes after human guidance) instead of the roadmap's `kms_capture` and `kms_classify`. We choose this because `kms_capture` would invoke a separate AI to re-process content the chat AI already understands (wasteful and lossy), and `kms_classify` would re-run classification on CLUELESS notes that already failed classification (same input, same output). The replacement tools let the chat AI act on human judgment directly.

**Status:** accepted (2026-06-11 — `kms_move` path verified feasible (move→write→reindex); the `kms_write` half remains blocked on TD-056 field-level metadata guard)

## Context

- The roadmap (Phase 4 — MCP Server MVP) specifies three MCP tools: `kms_search`, `kms_capture`, `kms_classify`.
- `kms_capture` as designed: receives `{content: string, source_type: string}`, passes content to the capture pipeline which invokes a separate AI to summarize, extract metadata, and classify. The chat AI that received the user's content has no input into this process.
- `kms_classify` as designed: receives `{note_path: string}`, re-runs the classify pipeline on a note. For CLUELESS notes (where classification already failed), this produces the same CLUELESS result.
- The capture pipeline (P2-CIC, merged 2026-06-08) already runs classify inline during capture. Watcher-triggered capture handles file drops automatically.
- CLUELESS notes have `classify_reasoning`, `classify_confidence`, `suggested_project`, and `suggested_primary_domain` stamped in frontmatter (verified in `pipelines/capture.py`), explaining why classification failed.
- The `_merge_metadata` function in `vault/writer.py` treats `incoming` as authoritative for ALL fields except `created`. Content hash change triggers full pipeline re-run, overwriting all metadata including user-set tags and project.

## Decision

### 1. `kms_capture` is NOT an MCP tool; replaced by `kms_write` (TD-056)

**Problem with `kms_capture`:** User tells chat AI "capture this discussion for me." `kms_capture` would take the text, pass it to a different AI instance (the capture pipeline's summarizer), which re-summarizes content the chat AI already processed. The chat AI's understanding of user intent (which project this belongs to, what tags to apply, what's important) is lost.

**`kms_write` instead:** Chat AI creates a `.md` note with frontmatter reflecting user intent (tags, project, summary as the AI understood them from conversation). File lands in vault. Watcher detects and runs capture pipeline. Pipeline processes but preserves user-set metadata fields.

**Blocker:** Requires a field-level metadata guard in the capture pipeline (TD-056) so the pipeline doesn't overwrite user/AI-set fields like `tags` and `project` when it re-processes the note. Without this guard, the watcher-triggered capture overwrites everything. Design of the guard mechanism is deferred.

### 2. `kms_classify` is NOT an MCP tool; replaced by `kms_move` (TD-057)

**Problem with `kms_classify`:** CLUELESS notes failed classification because the AI couldn't determine the correct destination. Re-running `kms_classify` on the same note with the same content produces the same CLUELESS result. The classification failure is already explained in the note's `classify_reasoning` frontmatter.

**`kms_move` instead:** When user asks "classify my inbox," the chat AI:
1. Reads CLUELESS notes via `kms_read` (which returns `classify_reasoning` in metadata)
2. Presents the AI's reasoning to the user ("I wasn't sure if this belongs to Movies or Game because...")
3. Asks user for guidance
4. Moves the note directly to the user-specified folder via `kms_move`

This turns a failed-AI-retry into a human-judgment resolution. The CLUELESS note gets resolved with information the classify pipeline never had — the user's explicit intent.

**`kms_move` implementation:** Thin wrapper around `move_note()` + `documents.replace_path()`. Updates `project`/`primary_domain` frontmatter to match destination. Uses `move_guard` to prevent watcher re-homing. Not blocked by TD-056 — ships in Phase 4 MVP.

## Considered Options

- **Roadmap design: `kms_capture` + `kms_classify` as MCP tools** — Direct pipeline invocation from AI. Simple, matches existing architecture. Rejected because: (a) `kms_capture` discards chat AI's understanding of user intent by passing content to a separate AI, (b) `kms_classify` re-runs a failed classification with no new information, (c) both push work to AI that should be resolved by human judgment in the conversation.
- **`kms_write` drops to inbox, no metadata** — AI writes raw content, pipeline handles everything. Avoids the field-level guard problem. Rejected because user intent about project/tags is lost — the whole point of conversational capture is that the AI knows what the user wants.
- **`kms_classify` with user hints** — Add a `hint` parameter so AI can pass user context to the classifier. Partially addresses the CLUELESS problem but adds complexity to the classify pipeline for a case better solved by direct move. Rejected.

## Consequences

- **Positive:** CLUELESS notes get resolved through human judgment (the one thing the classify pipeline lacked). `kms_move` ships in Phase 4 MVP — no blockers.
- **Positive:** Conversational capture preserves user intent about tags, project, and organization — the chat AI acts as a smart intermediary, not a dumb pipe.
- **Negative:** `kms_write` is blocked by TD-056 (field-level metadata guard). Phase 4 MVP ships without write capability. Users cannot create notes via MCP until the guard is designed and built. This is a core use case ("capture this discussion") that is deferred.
- **Negative:** Contradicts the roadmap's Phase 4 acceptance criteria ("Ask Claude: 'Capture this: <paste text>' — creates a new note in inbox"). The roadmap must be updated.
- **Risk:** Field-level guard design (TD-056) may prove complex. If it takes too long, consider an interim `kms_write` that writes to inbox with a `_preserve_metadata: true` frontmatter flag as a simpler guard mechanism.
