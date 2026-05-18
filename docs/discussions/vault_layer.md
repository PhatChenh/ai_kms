# Discussions: vault_layer

## Phase 0 — Planning
_Completed: 2026-05-14_

### Exchanges

**Q:** In OQ-V2, what is the `documents` table? Is it the vault's documentation folder or a SQL table? What does "FS" mean? What is `storage/documents.upsert()`? And what are all the options for keeping them in sync?

**A:** `documents` is a SQLite table defined in `storage/schema.sql` — not the vault folder. It is an index: one row per note in the vault, holding only metadata needed for fast lookups. The note body never lives there.

```sql
CREATE TABLE documents (
    id               INTEGER PRIMARY KEY,
    vault_path       TEXT UNIQUE,        -- "inbox/foo.md"
    updated_by_human INTEGER DEFAULT 0,  -- 0 or 1
    content_hash     TEXT,
    ...
);
```

FS = filesystem. The actual `.md` file on disk inside the Obsidian vault. Every note exists in two places simultaneously: the `.md` file (source of truth for content) and a row in `documents` (an index that mirrors a few frontmatter fields for cheap queries).

`storage/documents.upsert(...)` does not exist yet. It is a placeholder name for "a function that does `INSERT OR REPLACE` into the `documents` table." It would live in `storage/` alongside the existing `storage/audit_log.py`.

**Why a mirror at all:** A pipeline asking "is `inbox/foo.md` off-limits because the human edited it?" has two options — (A) open the file, parse YAML, read `updated_by_human` (slow: disk read + YAML parse), or (B) `SELECT updated_by_human FROM documents WHERE vault_path = ?` (fast: one indexed lookup). The project chose B.

**Option (a) — writer writes both FS and SQLite:**
```
writer.write_note(...)
  ├─ atomic write to .md file on disk
  └─ storage.documents.upsert(...)  ← writer calls this too
```
Pro: mirror always fresh. Con: `vault/writer.py` imports from `storage/` — vault layer is coupled to the database. A test needs a real SQLite file.

**Option (b) — writer touches only the FS; indexer syncs SQLite later:**
```
writer.write_note(...)
  └─ atomic write to .md file — done

later: indexer.detect_changes() scans vault, upserts changed rows
```
Pro: writer is pure-FS, testable with just `tmp_path`. Con: drift window between write and next `detect_changes()` call; every caller must remember to run the indexer.

**Option (c) — hybrid (what was chosen):** writer returns a `WriteOutcome` dataclass containing `vault_path`, `content_hash`, `updated_by_human`, etc. The writer imports nothing from storage. The pipeline calls `storage.documents.upsert(outcome)` as the next step after the write.
```
writer.write_note(...) → Success(WriteOutcome(...))

pipeline then calls:
storage.documents.upsert(outcome)  ← one extra line in the pipeline
```
This keeps the writer decoupled (testable with `tmp_path` only) while avoiding the full-vault rescan cost of option (b). The coupling moves up to the pipeline layer, which is allowed to know about both vault and storage.

_Key concept: vault-storage sync strategies — FS-only writer vs direct mirror vs WriteOutcome handoff_

---

## Phase 2 — frontmatter.py
_Completed: 2026-05-15_

### Exchanges

**Q:** What happens when a human creates a note in Obsidian, fills in some (but not all) frontmatter fields — some in the NoteMetadata known set, some not — and then the AI pipeline runs? Does filling in frontmatter cause the system to flag the note as edited by human?

**A:** `parse()` splits frontmatter into known keys (on `NoteMetadata`) and unknown keys (everything else). Known fields like `project: Zalopay` land directly on the model; unknown fields like `my_custom_tag: important` land in `extra`. Both survive a round-trip through `dumps()` unchanged.

Filling in frontmatter does NOT set `updated_by_human=True`. There is no watcher in Phase 0/1 — the vault layer cannot detect an Obsidian save. `updated_by_human` only becomes `True` when `write_note(..., actor="human")` is explicitly called. Until then, `updated_by_human` defaults to `False` and AI writes proceed normally.

The real gap is different: when the AI pipeline writes a fresh `NoteMetadata` (e.g. `project=None`, `tags=[]`), `dumps()` uses `exclude_none=True` — which only drops `None`, not empty lists. So `tags: []` gets written to the file, silently overwriting the human's `tags: [meeting, Q2]`.

```python
# model_dump(exclude_none=True) on default NoteMetadata:
# {'tags': [], 'updated_by_human': False}
# tags=[] is NOT None → it IS written → human tags are lost
```

Fix applied (Option B in Phase 4 plan): in `write_note` merge, if caller's value is `None` or `[]`, fall back to the existing note's value instead of overwriting.

_Key concept: updated_by_human is not auto-set; known-field overwrite gap from exclude_none_

---

**Q:** What is a sentinel value, and why is it needed?

**A:** Option B rule: `tags=[]` from the caller means "I didn't provide tags — keep existing." But what if a pipeline genuinely wants to clear all tags? It would also pass `tags=[]`, which is indistinguishable from "not provided."

A sentinel is a special magic value that breaks this ambiguity — a value that can only mean "I explicitly want this to be empty":

```python
NoteMetadata(tags=["__clear__"])
# writer sees: non-empty → "explicitly provided"
# writer strips sentinel → writes tags: []
```

Without a sentinel, there is no way to tell "forgot to set" from "wants empty." The proper fix (TD-014) is a `NoteMetadataUpdate` dataclass where every field has three states: a value, `None` (not provided), and `UNSET` (explicitly cleared). This is deferred because Phase 1 pipelines only add data, never clear fields.

_Key concept: sentinel value — distinguishing "not provided" from "explicitly empty"_

---

**Q:** Should design gaps like the sentinel issue be tracked somewhere so they are not forgotten?

**A:** Yes — in STATE.md under Technical Debt. TD entries record: what the gap is, why it is deferred, what the known fix is, and which phase owns it. A `⚠️ USER FLAG` annotation in the TD entry tells Claude Code to surface the issue loudly before any future phase extends the workaround silently.

_Key concept: TD entries as cross-session design memory with escalation flags_

---

## Review Session — CLAUDE.md rename + vault layer audit
_Completed: 2026-05-18_

Context: vault layer fully implemented. This session reviewed `writer.py` /
`indexer.py` / `frontmatter.py` and assessed the impact of renaming the
`project_index.md` / `domain_index.md` navigation files to `CLAUDE.md` (so they
double as project/domain instructions for Claude products).

### Exchanges

**Q:** Does `write_note` let the AI edit only the frontmatter, or the note body too?

**A:** `write_note` is a whole-file replace, not a frontmatter stamper. Two different merge behaviours live in one function: **metadata is merged** (`_merge_metadata`, Option B — caller's fields layered over existing), but **body is not merged — it is a total overwrite**. Whatever string the caller passes as `content` becomes the entire body; the old body is discarded. There is no body-aware editing primitive in the vault layer.
```python
def write_note(path, content, metadata, actor):
    body = content.rstrip("\n")          # caller's content = the WHOLE new body
    merged = _merge_metadata(...)        # metadata merged (Option B)
    rendered = dumps(merged, body)       # old body gone
```
_Key concept: write_note — body is whole-replace, metadata is merged_

**Q:** Why was the inner `try/except` block in `move_note` deleted? I expected more code added, not removed.

**A:** Only the **inner nested** `try/except OSError` (the EXDEV cross-filesystem branch) was removed; the outer `try...except Exception → Failure` stayed. The EXDEV branch existed because the old code did `os.replace(src, dst)` directly — src and dst can be on different filesystems, which raises `OSError errno 18 (EXDEV)`. The new code never replaces src→dst directly: `_atomic_write` writes a tmp file **in `dst.parent`**, then `os.replace(tmp, dst)`. tmp and dst are always in the same directory = same filesystem → `os.replace` cannot raise EXDEV. The branch became dead code. The fix was structural (collapse two steps into one atomic step), not additive — correct fixes can be *less* code.
_Key concept: EXDEV avoided by writing the tmp file in the destination directory_

**Q:** Explain the same-filesystem partial-write window in the old `move_note` in detail.

**A:** The old same-fs path was two separate operations: `os.replace(src, dst)` (move the file — dst now has the *original* content), then `_atomic_write(dst, rendered)` (rewrite with merged metadata). A crash or disk error **between** the two left src already gone, dst present but with stale un-merged metadata, and the function returning `Failure(recoverable=False)`. The caller sees `Failure`, believes the move failed, but the file *is* at dst and src is gone — state contradicts the return value, and no retry is possible. The fix writes merged content to a tmp and `os.replace`s it into dst as one atomic step, unlinking src only after dst is fully written — so any failure leaves src intact and retryable.
_Key concept: multi-step file mutation creates a crash window; collapse to one atomic step_

**Q:** What is the `post = _fm.Post(...)` line in `frontmatter.py`? Is it scaffolding for a later stage?

**A:** It is dead code. `post` is assigned and never read — the function builds the YAML header manually below (`yaml.dump` with a custom block-style dumper). The line is a leftover from an earlier approach that used `python-frontmatter`'s own `dumps()`; when that was replaced (to force Obsidian-compatible block-style lists) the `_fm.Post` construction should have been deleted and was not. Nothing later reads it — safe to remove.
_Key concept: identifying dead code vs future scaffolding — trace whether the symbol is ever read_

**Q:** `write_note` always emits a YAML frontmatter block — why, and what does that mean for `CLAUDE.md`?

**A:** `_merge_metadata` unconditionally sets three fields — `created`, `updated`, `updated_by_human`. `dumps` does `model_dump(exclude_none=True)`; those three are never `None`, so the rendered dict is always non-empty and the `---` block is always emitted. Any file written via `write_note` therefore gets machine frontmatter. For `CLAUDE.md` (an instructions/index file) that means a Properties panel of meaningless metadata in Obsidian and a YAML block at the top of the file Claude products read as instructions — noise, not breakage.
_Key concept: write_note unconditionally emits frontmatter — every written file is treated as a note_

**Q:** Can `updated_by_human` just be set `True` on `CLAUDE.md`, since the field becomes redundant once the co-author solution lands?

**A:** No — `updated_by_human` is an active gate, not a passive tag. `write_note` checks `if existing.updated_by_human and actor == "ai": return Failure(recoverable=False)`. Setting it `True` permanently locks the AI out of the file — which kills "AI maintains the index" (Option A). The gate is binary and whole-note: **no value lets both AI and human write** — `True` blocks AI, `False` allows AI. That is exactly the limitation the future co-author solution exists to fix. For Option A the field must stay `False`, which is its natural state for `actor="ai"` writes (`_merge_metadata` sets `updated_by_human = (actor == "human")`).
```python
if existing_note.metadata.updated_by_human and actor == "ai":
    return Failure(error="note locked by human edit", recoverable=False, ...)
```
_Key concept: updated_by_human is an active write-gate; True = AI locked out, not a cosmetic flag_

**Q:** Why add `project_index(name)` / `domain_index(name)` helpers to `paths.py` instead of letting callers build the path?

**A:** `paths.py` had `project_dir(name)` but nothing returning the index file itself, so every pipeline touching the index would hardcode the `"CLAUDE.md"` string literal. Scattered literals mean a future rename is a hunt-every-file change. A helper puts the filename in one place — callers do `project_index(name)`, never type `"CLAUDE.md"` — so a rename is a one-line edit. Same single-source-of-truth principle the codebase applies to prompts and thresholds.
```python
def project_index(name: str) -> Path:
    """Return Projects/<name>/CLAUDE.md path."""
    return project_dir(name) / "CLAUDE.md"
```
_Key concept: single source of truth for a constant — wrap a magic filename in a helper_

**Q:** The indexer indexes every `.md` file — should `CLAUDE.md` be treated as a captured document?

**A:** No. `scan_vault` indexes every `.md`, so `CLAUDE.md` would land in the `documents` table, FTS5 search, and the capture/classify pipelines — but it is a navigation index and instructions file, not a captured note. Adding it to `indexer.IGNORE_FILES` excludes it from the documents mirror, search, and capture, while leaving AI co-authoring via `vault/writer.py` untouched. The ignore-set affects only the indexer/capture path — it has no effect on `write_note`, which is a separate decision.
```python
IGNORE_FILES = frozenset({".DS_Store", "Thumbs.db", "CLAUDE.md"})
```
_Key concept: indexer IGNORE_FILES — separating captured notes from structural/index files_

**Q:** Whole-body replace seems dangerous for Phase 1/2 — when the AI summarizes a dropped `.md`, could the system wipe the original body and replace it with just the summary?

**A:** Not in the designed flow. Roadmap Phase 1 specifies: a `.md` drop → AI writes the summary into the **`summary` frontmatter field**, in place — the body stays the original content. The capture pipeline reads the drop, keeps `note.content`, and passes it back to `write_note` as `content` unchanged; only the metadata gains `summary`. The wipe happens only if a pipeline is miscoded to pass the summary string as `content`. `write_note` cannot defend against this — `content` is an opaque string with no signal distinguishing "summary" from "full body". The mitigation is a Phase-1 discipline plus an acceptance test (drop a `.md` with a known body → run capture → assert the body is byte-identical and `summary` is populated), not a vault-layer change.
_Key concept: capture flow puts the summary in frontmatter, not the body — the original note is preserved_

**Q:** `CLAUDE.md` mixes an AI-maintained index with human-authored context in one file — does the whole-note gate handle that?

**A:** It does not, and that is tracked debt (TD-015). `updated_by_human` is whole-note (DECISION-002) and `write_note` replaces the whole body, so the gate cannot distinguish an AI index section from a human context section living in the same body. The future watcher must do more than flip `updated_by_human` — it needs a section-aware body merge (e.g. `<!-- AI-INDEX -->...<!-- /AI-INDEX -->` delimiters) so AI index updates do not clobber human context edits. Interim rule (Option A): AI writes `CLAUDE.md` with `actor="ai"`, the flag stays `False`, and human context edits can be overwritten until the section-merge lands.
_Key concept: whole-note authorship gate cannot express a co-authored file with distinct AI/human sections_
