# Plan: vault_layer
_Last updated: 2026-05-14_
_Status: [ ] pending_

## Architecture

```
                ┌──────────────────────────────────────────────┐
                │   core/config.py  (exists)                   │
                │   CONFIG.main.vault.{root, inbox_path, ...}  │
                └───────┬──────────────────────────────────────┘
                        │ lazy import inside function bodies
                        ▼
┌──────────────────────────────────────────────────────────────────────┐
│                          vault/  (this plan)                         │
│                                                                      │
│  ┌────────────────────┐    ┌──────────────────┐    ┌───────────────┐ │
│  │ paths.py           │    │ frontmatter.py   │    │ reader.py     │ │
│  │ project_dir(name)  │    │ NoteMetadata     │◀───│ read_note()   │ │
│  │ domain_dir(name)   │    │ parse / dumps    │    │ → Note(hash)  │ │
│  │ documentation(p)   │    └────────┬─────────┘    └────────┬──────┘ │
│  │ briefings_for(d)   │             │                       │        │
│  │ briefings_today()  │             ▼                       ▼        │
│  │ synthesis_week(d)  │      ┌──────────────────────────────────┐    │
│  │ (parametrized only)│      │ writer.py  (load-bearing)        │    │
│  └────────────────────┘      │ write_note(path, content, meta,  │    │
│                              │   source: 'ai'|'human') →        │    │
│                              │   Result[WriteOutcome]           │    │
│                              │ move_note(src, dst, source) →    │    │
│                              │   Result[WriteOutcome]           │    │
│                              │ • read existing → check          │    │
│                              │   updated_by_human gate          │    │
│                              │ • atomic: tmp + fsync +          │    │
│                              │   os.replace                     │    │
│                              │ FS-only — no SQLite import       │    │
│                              └──────────┬───────────────────────┘    │
│                                         │ WriteOutcome carries       │
│                                         │  vault_path, content_hash, │
│                                         │  updated_by_human, ...     │
│                              ┌──────────▼───────────────────────┐    │
│                              │ indexer.py                       │    │
│                              │ scan_vault(root) → [VaultEntry]  │    │
│                              │ detect_changes(entries) →        │    │
│                              │   ChangeSummary(added,           │    │
│                              │     modified, deleted, moved)    │    │
│                              │ moved = del+add same hash        │    │
│                              └──────────┬───────────────────────┘    │
└─────────────────────────────────────────┼────────────────────────────┘
                                          │ read/write via
                                          ▼
                              ┌─────────────────────────────────┐
                              │ storage/documents.py (NEW)      │
                              │ upsert(WriteOutcome) → Result   │
                              │ sync_path(vault_path)           │
                              │ get_by_path(vault_path)         │
                              │ all_paths() → [(path, hash)]    │
                              │ delete_by_path(vault_path)      │
                              │ rename(old, new)                │
                              └────────┬────────────────────────┘
                                       │ uses
                                       ▼
                              ┌─────────────────────────────────┐
                              │ storage/db.py  (exists)         │
                              │ get_connection()                │
                              │ documents table                 │
                              └─────────────────────────────────┘

                       ┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
                          pipelines/capture.py (Phase 1, future) calls
                       │  writer.write_note(...) → WriteOutcome,        │
                          then storage.documents.upsert(outcome).
                       │  retrieval/* (Phase 3) calls indexer.           │
                       └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘
```

**write_note sequence**

```
  caller             writer.py            frontmatter.py        FS
    │                   │                       │                │
    │ write_note(...,   │                       │                │
    │   source="ai")    │                       │                │
    │──────────────────▶│                       │                │
    │                   │ exists? parse(path)   │                │
    │                   │──────────────────────▶│                │
    │                   │ ◀─(NoteMetadata, body)│                │
    │                   │                                        │
    │                   │ existing.updated_by_human + source=ai? │
    │ ◀─Failure(rec=F)──│   yes ────────────────                 │
    │                   │   no → merge meta                      │
    │                   │   (keep created; bump updated;         │
    │                   │    set updated_by_human ← source)      │
    │                   │ dumps(meta, body) ───▶│                │
    │                   │ ◀── rendered str ─────│                │
    │                   │                                        │
    │                   │ tmp in path.parent                     │
    │                   │ write + flush + fsync ────────────────▶│
    │                   │ os.replace(tmp, path) ────────────────▶│
    │ ◀─Success(        │                                        │
    │     WriteOutcome) │                                        │
    │                   │                                        │
    │ storage.documents.upsert(outcome) ─────▶ SQLite            │
```

**updated_by_human ratchet (whole-note)**

```
                  ┌───────────────────┐
   first ai write │  ai-owned         │
   ─────────────▶ │  updated_by_      │
                  │   human = False   │
                  └─┬───────────────┬─┘
        source=ai   │               │  source=human (any write)
        re-write OK │               ▼
                    │           ┌───────────────────┐
                    │           │  human-owned      │
                    │           │  updated_by_      │
                    │           │   human = True    │
                    │           └─┬───────────────┬─┘
                    │             │ source=ai     │ source=human
                    │             ▼               ▼
                    │       Failure(rec=False)   stays human
                    ▼
                stays ai-owned
```

## Approach

Build the five vault modules **bottom-up** so each phase has only the dependencies already in
place: paths → frontmatter → reader → writer → indexer. Add one storage-layer sibling
(`storage/documents.py`) between writer and indexer to host the SQLite mirror upsert, so the
writer stays FS-only and pipelines glue the two layers with one extra call. Every phase ships
its tests in the same commit; nothing depends on a future phase to be verifiable.

Three design decisions confirmed before drafting:

1. **paths.py** exposes only **parametrized** helpers. Static folder roots stay on
   `VaultConfig` properties already in `core/config.py`. No duplication.
2. **writer.py** is **FS-only**. It returns `WriteOutcome` carrying everything needed to
   upsert SQLite. Callers (pipelines) call `storage.documents.upsert(outcome)` as the next
   step. The "tiny addition" — a one-line helper that pipelines invoke — lives in
   `storage/documents.py`, not in the vault layer.
3. **indexer.py** returns a `ChangeSummary` with **four** sets: `added`, `modified`,
   `deleted`, `moved`. Move detection is mandated by STATE.md DECISION-001 and saves Phase 1
   from re-implementing it.

Remaining minor questions (OQ-V4 PyYAML bool quirk, OQ-V5 hash whitespace, OQ-V6 NFC
normalization, OQ-V7 dir fsync) carry recommended defaults per phase. Override in code
review if needed.

## Phases

### Phase 1 — paths.py
**Goal**: Parametrized vault-path helpers that ensure target directories exist.

**Steps**:
1. Create `vault/__init__.py` (empty) and `vault/paths.py`.
2. Inside `paths.py`, import `CONFIG` lazily — every function body does
   `from core.config import CONFIG` rather than importing at module top, matching
   [storage/db.py:46-47](storage/db.py#L46-L47).
3. Implement six functions, each returning `pathlib.Path`. Each calls
   `mkdir(parents=True, exist_ok=True)` on the directory the caller will write into:
   - `project_dir(name: str) -> Path`            → `projects_path / name`
   - `domain_dir(name: str) -> Path`             → `domain_path / name`
   - `documentation(project: str) -> Path`       → `documentation_path / f"{project}.md"`
     (mkdir the parent, not the file)
   - `briefings_today() -> Path`                 → `briefings_path / f"{date.today().isoformat()}.md"`
   - `briefings_for(d: date) -> Path`            → `briefings_path / f"{d.isoformat()}.md"`
   - `synthesis_week(d: date) -> Path`           → `synthesis_path / f"{iso_week_filename(d)}.md"`
     where `iso_week_filename(date(2026, 4, 25))` returns `"2026-W17.md"` via
     `d.isocalendar()`.
4. ISO date format only — `2026-04-25.md`, not `12_04`-style.
5. Add a module docstring documenting: no static folder helpers (use
   `CONFIG.main.vault.inbox_path` etc. directly); functions create dirs but do not write
   files.

**Files to modify**:
- `vault/__init__.py` — new, empty.
- `vault/paths.py` — new module.
- `tests/test_vault/__init__.py` — new, empty.
- `tests/test_vault/test_paths.py` — new tests.
- `tests/test_vault/conftest.py` — new fixture `vault_root(tmp_path)` that builds an
  empty vault skeleton (inbox/, Projects/, Domain/, ..., Briefings/, Synthesis/), then
  monkeypatches `core.config.CONFIG.main.vault.root` to point at it. Used by every
  vault test from Phase 2 onward.

**Test criteria**:
- [ ] `test_project_dir_returns_subfolder_of_projects` — `project_dir("X")` returns
      `<root>/Projects/X` and the directory exists.
- [ ] `test_documentation_returns_md_path_with_parent_created` — `documentation("Y")`
      returns `<root>/Documentation/Y.md`, the file does NOT exist (no write), the parent
      directory does.
- [ ] `test_briefings_today_iso_format` — `briefings_today()` ends with
      `f"{date.today().isoformat()}.md"`.
- [ ] `test_briefings_for_specific_date` — `briefings_for(date(2026, 4, 25))` ends with
      `2026-04-25.md`.
- [ ] `test_synthesis_week_iso_week_format` — `synthesis_week(date(2026, 4, 25))` ends
      with `2026-W17.md`.
- [ ] `test_helpers_idempotent` — calling each helper twice does not raise and the
      directory still exists.
- [ ] Run: `uv run pytest tests/test_vault/test_paths.py -v`

**Status**: [ ] pending

---

### Phase 2 — frontmatter.py
**Goal**: A typed wrapper around `python-frontmatter` that other modules use instead of
the library directly.

**Steps**:
1. Create `vault/frontmatter.py`.
2. Define `NoteMetadata` (Pydantic `BaseModel`):
   - `type: str | None = None`
   - `tags: list[str] = Field(default_factory=list)`
   - `project: str | None = None`
   - `domain: str | None = None`
   - `created: date | None = None`
   - `updated: datetime | None = None`
   - `confidence: float | None = Field(default=None, ge=0.0, le=1.0)`
   - `updated_by_human: bool = False`
   - `summary: str | None = None`
   - `source: str | None = None`
   - `status: str | None = None`
   - `extra: dict[str, Any] = Field(default_factory=dict)`
   - `model_config = {"extra": "ignore"}` — unknown keys are pre-extracted into `extra` by
     `parse`, never passed to the model.
3. Add a `field_validator(mode="before")` on every `str | None` field (`type`, `project`,
   `domain`, `summary`, `source`, `status`) that coerces a `bool` to its lowercase string
   form (`True` → `"yes"`, `False` → `"no"`). This neutralises the **PyYAML 1.1 bool
   quirk** (OQ-V4) — a frontmatter value of `status: on` becomes the string `"on"` rather
   than blowing up the model. Log a debug line when coercion fires.
4. `def parse(path: Path) -> Result[tuple[NoteMetadata, str]]`:
   - `frontmatter.load(path)` → `Post`.
   - Split `Post.metadata` into "known" (keys present on `NoteMetadata`) and "unknown" (the
     rest); pass unknown as `extra={...}` to the model constructor.
   - Return `Success((metadata, post.content))`. Wrap
     `FileNotFoundError`, `yaml.YAMLError`, `UnicodeDecodeError`,
     `pydantic.ValidationError` as `Failure(recoverable=False, ...)` each with a
     descriptive `error` and a `context` dict including `path`.
5. `def dumps(metadata: NoteMetadata, body: str) -> str`:
   - Build a dict from `metadata.model_dump(exclude_none=True, exclude={"extra"})`.
   - Merge `metadata.extra` on top (unknown user keys round-trip unchanged).
   - Construct `frontmatter.Post(content=body, **merged_dict)`.
   - Return `frontmatter.dumps(post)`.
6. Module docstring: callers MUST NOT import `frontmatter` (the library) directly.

**Files to modify**:
- `vault/frontmatter.py` — new.
- `tests/test_vault/test_frontmatter.py` — new tests.

**Test criteria**:
- [ ] `test_parse_minimal_note` — file with `---\ntitle: T\n---\nbody`, parse succeeds,
      `metadata.extra == {"title": "T"}` (title isn't on `NoteMetadata`).
- [ ] `test_parse_no_frontmatter` — file with no `---` block, `metadata` has all
      defaults, body is the whole file.
- [ ] `test_parse_unknown_fields_preserved` — `custom_field: 5` lands in `metadata.extra`.
- [ ] `test_parse_pyyaml_bool_quirk_coerced` — `status: yes` parses, `metadata.status ==
      "yes"`, no exception.
- [ ] `test_parse_malformed_yaml_returns_failure` — unterminated frontmatter returns
      `Failure(recoverable=False)`, error mentions the path.
- [ ] `test_parse_missing_file_returns_failure` — non-existent path returns `Failure`.
- [ ] `test_dumps_round_trips_known_fields` — `parse(dumps(meta, body))` returns the same
      metadata for every field on the model.
- [ ] `test_dumps_round_trips_extra_fields` — keys in `metadata.extra` survive the round
      trip.
- [ ] `test_dumps_uses_block_list_tags` — emitted YAML contains `tags:\n- a\n- b`, not
      `tags: [a, b]`. (Obsidian compatibility.)
- [ ] Run: `uv run pytest tests/test_vault/test_frontmatter.py -v`

**Status**: [ ] pending

---

### Phase 3 — reader.py
**Goal**: One function that loads a note from disk and returns a hashed, typed `Note`.

**Steps**:
1. Create `vault/reader.py`.
2. Define `Note` as `@dataclass(frozen=True)` with: `path: Path`, `metadata: NoteMetadata`,
   `content: str` (body, no frontmatter), `content_hash: str` (full SHA-256 hex of
   `body.rstrip("\n").encode("utf-8")`).
3. The `rstrip("\n")` step resolves **OQ-V5** — `frontmatter.dumps` adds a trailing
   newline; without strip, a round-trip would flip the hash and trigger phantom `modified`
   in the indexer. Document the rule in a module docstring.
4. `def read_note(path: Path) -> Result[Note]`:
   - Call `vault.frontmatter.parse(path)`; on `Failure`, return it unchanged.
   - On success, compute the hash and return `Success(Note(...))`.

**Files to modify**:
- `vault/reader.py` — new.
- `tests/test_vault/test_reader.py` — new tests.

**Test criteria**:
- [ ] `test_read_note_returns_note_with_hash` — write a fixture file, read it, assert
      `content_hash` is 64 hex chars and matches an independent
      `sha256(body.rstrip("\n").encode()).hexdigest()`.
- [ ] `test_read_note_hash_stable_across_trailing_newlines` — two files with bodies
      `"x"` and `"x\n"` produce the same hash. (Phantom-modified guard.)
- [ ] `test_read_note_propagates_parse_failure` — malformed YAML → `Failure` from reader
      with the path in `context`.
- [ ] `test_read_note_missing_file` — non-existent path → `Failure(recoverable=False)`.
- [ ] `test_read_note_returns_metadata_object` — parsed `metadata` is a `NoteMetadata`
      instance, not a raw dict.
- [ ] Run: `uv run pytest tests/test_vault/test_reader.py -v`

**Status**: [ ] pending

---

### Phase 4 — writer.py (load-bearing)
**Goal**: Atomic, idempotent vault writes with the `updated_by_human` safety gate.

**Steps**:
1. Create `vault/writer.py`.
2. Define `WriteOutcome` as `@dataclass(frozen=True)` with: `vault_path: str` (POSIX,
   relative to vault root), `absolute_path: Path`, `content_hash: str`,
   `metadata: NoteMetadata`. This is what callers feed to
   `storage.documents.upsert` in Phase 5.
3. Define `WriteSource = Literal["ai", "human"]`.
4. `def write_note(path: Path, content: str, metadata: NoteMetadata, source: WriteSource)
   -> Result[WriteOutcome]`:
   - `body = content.rstrip("\n")` (one canonical newline policy — matches reader).
   - If `path.exists()`:
     - `vault.reader.read_note(path)` to get existing `Note`. On `Failure`, propagate.
     - If `existing.metadata.updated_by_human` and `source == "ai"`:
       return `Failure(error="note locked by human edit", recoverable=False,
       context={"path": str(path), "vault_path": _to_vault_path(path)})`.
     - Preserve `existing.metadata.created` (don't let AI overwrite the original date).
   - Else, treat `existing` as None and seed `created = metadata.created or
     date.today()`.
   - Build the merged metadata:
     - `created` per above
     - `updated = datetime.now(timezone.utc)`
     - `updated_by_human = (source == "human")`
     - All other fields from the caller's `metadata`
     - Preserve `extra` from existing if caller didn't provide one
   - Render with `vault.frontmatter.dumps(merged_metadata, body)`.
   - Atomic write:
     ```python
     tmp = path.parent / f".tmp_{uuid4().hex}.md"   # dot-prefix → indexer skips
     with tmp.open("w", encoding="utf-8") as f:
         f.write(rendered)
         f.flush()
         os.fsync(f.fileno())
     os.replace(tmp, path)
     ```
     On any exception, `tmp.unlink(missing_ok=True)` then return `Failure`.
   - Compute `content_hash = sha256(body.encode()).hexdigest()`.
   - Return `Success(WriteOutcome(vault_path=_to_vault_path(path), absolute_path=path,
     content_hash=content_hash, metadata=merged_metadata))`.
5. `def move_note(src: Path, dst: Path, source: WriteSource) -> Result[WriteOutcome]`:
   - `vault.reader.read_note(src)` to get current note. Propagate `Failure`.
   - If `current.metadata.updated_by_human` and `source == "ai"`: same `Failure` as write.
   - `dst.parent.mkdir(parents=True, exist_ok=True)`.
   - Bump `updated`, set `updated_by_human ← (source == "human")`.
   - **Same-filesystem fast path**: `os.replace(src, dst)` atomically, then re-write `dst`
     with merged metadata via the atomic-write recipe above. The two-step is needed because
     `os.replace` cannot update content; only the location.
   - **Cross-filesystem fallback** (`OSError: [Errno 18]`): write the rendered content to a
     `.tmp_*` next to `dst`, fsync, `os.replace(tmp, dst)`, then `src.unlink()`.
   - Return `Success(WriteOutcome(...))` with `vault_path` reflecting the destination.
6. Private helper `_to_vault_path(absolute: Path) -> str` — computes
   `absolute.relative_to(CONFIG.main.vault.root).as_posix()` (NFC normalize via
   `unicodedata.normalize("NFC", ...)` — OQ-V6 default ON; cheap insurance for macOS).
7. **Do not** call any `storage/` function. Writer is FS-only.
8. **Do not** fsync the parent directory (OQ-V7 default OFF — developer-grade durability).

**Files to modify**:
- `vault/writer.py` — new.
- `tests/test_vault/test_writer.py` — new tests.

**Test criteria**:
- [ ] `test_write_new_note_creates_file_with_frontmatter` — write to a path that doesn't
      exist; `path.exists()`, content parses back to the same metadata (minus
      `updated` which is server-set), body matches.
- [ ] `test_write_sets_updated_by_human_per_source` — write with `source="human"` →
      `updated_by_human=True` in the file; write with `source="ai"` → False.
- [ ] `test_write_ai_blocked_when_updated_by_human_true` — pre-create file with
      `updated_by_human: true`; attempt `write_note(..., source="ai")` returns
      `Failure(recoverable=False)`, file content unchanged byte-for-byte.
- [ ] `test_write_human_succeeds_over_updated_by_human` — same setup; `source="human"`
      succeeds and keeps `updated_by_human=True`.
- [ ] `test_write_preserves_created_on_overwrite` — re-write existing note; `created` stays
      original, `updated` advances.
- [ ] `test_write_idempotent_content_hash` — write the same content twice; `WriteOutcome
      .content_hash` is identical both times.
- [ ] `test_write_returns_write_outcome_with_relative_vault_path` —
      `outcome.vault_path == "inbox/foo.md"` for a vault at `<root>/inbox/foo.md`.
- [ ] `test_write_atomic_no_partial_on_failure` — monkeypatch `os.replace` to raise; assert
      original file (if any) is unchanged and no `.tmp_*` file remains in the parent.
- [ ] `test_write_temp_file_uses_dot_prefix` — patch `os.replace` to record the source
      filename; assert it starts with `.tmp_`.
- [ ] `test_move_note_changes_location_and_updates_metadata` — `move_note(a, b, "ai")`
      with non-locked `a`; `a` gone, `b` exists with bumped `updated`, body intact.
- [ ] `test_move_note_blocked_when_locked` — `a` locked; `move_note(a, b, "ai")` →
      `Failure`, both files in original state.
- [ ] `test_unicode_normalization_on_vault_path` — file at name containing decomposed
      Unicode (NFD); `outcome.vault_path` is NFC.
- [ ] Run: `uv run pytest tests/test_vault/test_writer.py -v`

**Status**: [ ] pending

---

### Phase 5 — storage/documents.py (SQLite mirror)
**Goal**: A small storage module that pipelines call after every `write_note` to keep the
`documents` table in sync with the FS.

**Steps**:
1. Create `storage/documents.py`. This is a **storage-layer** file (sibling of
   `audit_log.py`), not a vault file. It must contain no business logic — pure SQL plus
   the conversion from `WriteOutcome` to row.
2. Public functions (all return `Result[...]` per project rule):
   - `upsert(outcome: WriteOutcome, db_path: Path | None = None) -> Result[int]` — `INSERT
     OR REPLACE INTO documents (vault_path, title, summary, note_type, confidence,
     updated_at, updated_by_human, content_hash, created_at) VALUES (...)`. Uses
     `outcome.metadata` for column values. Returns rowid.
   - `get_by_path(vault_path: str, db_path=None) -> Result[DocumentRow | None]` — single
     `SELECT *` for the indexer / pipelines that need to ask "is this locked?".
   - `all_paths(db_path=None) -> Result[list[tuple[str, str]]]` — `SELECT vault_path,
     content_hash FROM documents`. Indexer uses this for diff.
   - `delete_by_path(vault_path: str, db_path=None) -> Result[int]` — for indexer
     handling of `deleted`.
   - `rename(old: str, new: str, db_path=None) -> Result[int]` — `UPDATE documents SET
     vault_path = ? WHERE vault_path = ?`. Indexer uses this on detected moves to preserve
     `documents.id` (DECISION-001).
3. Define `DocumentRow` as `@dataclass(frozen=True)` mirroring schema columns.
4. Title: derive from `outcome.metadata.extra.get("title")` or fall back to the filename
   stem. Document this choice — pipelines can override later by setting `title` themselves.
5. Wrap `sqlite3.Error` as `Failure(recoverable=False)` with `context={"vault_path":
   ..., "op": "upsert"|"get"|...}`.

**Files to modify**:
- `storage/documents.py` — new.
- `tests/test_storage/test_documents.py` — new tests.

**Test criteria**:
- [ ] `test_upsert_inserts_new_row` — build a `WriteOutcome` from a fake `Note`; upsert;
      `get_by_path` returns matching row.
- [ ] `test_upsert_replaces_existing_row` — upsert twice with different `content_hash`;
      `get_by_path` reflects the latest.
- [ ] `test_upsert_persists_updated_by_human` — outcome with metadata
      `updated_by_human=True` → row's `updated_by_human` column is 1.
- [ ] `test_all_paths_returns_path_hash_pairs` — upsert 3 outcomes; `all_paths` returns
      exactly those 3.
- [ ] `test_delete_by_path_removes_row` — upsert then delete; `get_by_path` returns
      `Success(None)`.
- [ ] `test_rename_updates_vault_path_preserves_id` — upsert, capture rowid, rename,
      assert new `vault_path` exists with the same rowid.
- [ ] `test_upsert_returns_failure_on_locked_db` — patch `get_connection` to raise
      `sqlite3.OperationalError`; result is `Failure(recoverable=False)`.
- [ ] Run: `uv run pytest tests/test_storage/test_documents.py -v`

**Status**: [ ] pending

---

### Phase 6 — indexer.py
**Goal**: Filesystem scan + diff against the SQLite mirror, emitting a four-set
`ChangeSummary`.

**Steps**:
1. Create `vault/indexer.py`.
2. Constants at module top:
   ```python
   IGNORE_DIRS  = frozenset({".git", ".obsidian", ".trash", ".stversions",
                              "node_modules", "_assets", "_system"})
   IGNORE_FILES = frozenset({".DS_Store", "Thumbs.db"})
   ```
3. Define dataclasses:
   - `VaultEntry` (frozen): `path: Path`, `vault_path: str`, `content_hash: str`,
     `metadata: NoteMetadata`.
   - `ChangeSummary` (frozen): `added: list[VaultEntry]`, `modified: list[VaultEntry]`,
     `deleted: list[str]`, `moved: list[tuple[str, VaultEntry]]`. The `moved` tuple is
     `(old_vault_path, new_entry)`.
4. `def scan_vault(root: Path | None = None) -> Result[list[VaultEntry]]`:
   - Lazy-import `CONFIG` when `root is None`.
   - Walk `root` recursively. Skip:
     - Any directory in `IGNORE_DIRS`.
     - Any directory whose name starts with `.` (catches `.tmp_*` writer artifacts).
     - Any file in `IGNORE_FILES`.
     - Any file containing `.sync-conflict-` in its name.
     - Any non-`.md` file (case-insensitive).
     - Any symlink (check `entry.is_symlink()` before recursing).
   - For each remaining `.md`, call `vault.reader.read_note(path)`. On `Failure`, append
     the error to a per-call `errors` list, skip the file, continue scanning (do not abort
     a full scan because one note has malformed YAML).
   - Build `VaultEntry` per success; return `Success([...])`. If `errors` is non-empty,
     log a single WARNING with the count and the first three paths, then still return
     `Success` — the scan is informational and partial results are useful.
5. `def detect_changes(current: list[VaultEntry], db_path: Path | None = None) ->
   Result[ChangeSummary]`:
   - `storage.documents.all_paths(db_path)` → `Result[list[(vault_path, content_hash)]]`.
     Propagate `Failure`.
   - Build sets: `current_by_path`, `db_by_path` (dict of `vault_path → content_hash`).
   - First pass:
     - `added_raw`  = entries in current not in db.
     - `deleted_raw` = paths in db not in current.
     - `modified`   = entries in both where hashes differ.
   - Move detection (DECISION-001):
     - For each `entry` in `added_raw`, look for a path in `deleted_raw` whose
       hash equals `entry.content_hash`. If exactly one match:
       move it to `moved` as `(deleted_path, entry)`, remove from both `added_raw` and
       `deleted_raw`. If zero or multiple matches: leave in `added_raw`.
   - Final lists: `added = added_raw`, `deleted = deleted_raw`, plus `modified` and
     `moved`.
   - Log: `"Scan complete: X added, Y modified, Z deleted, W moved (total N)"` at INFO.
6. The indexer **does not write** to `documents`. It returns a `ChangeSummary`; the caller
   (a Phase 1+ pipeline) decides what to apply (`upsert`/`delete_by_path`/`rename`).

**Files to modify**:
- `vault/indexer.py` — new.
- `tests/test_vault/test_indexer.py` — new tests.

**Test criteria**:
- [ ] `test_scan_finds_only_md_files` — fixture vault with `note.md`, `image.png`,
      `note.txt`; scan returns only `note.md`.
- [ ] `test_scan_skips_ignore_dirs` — fixture with `.git/foo.md`, `.obsidian/x.md`,
      `inbox/real.md`; scan returns only `inbox/real.md`.
- [ ] `test_scan_skips_dot_prefixed_dirs_and_files` — fixture with `.tmp_abc.md`,
      `.cache/x.md`; scan returns neither.
- [ ] `test_scan_skips_sync_conflict_files` — fixture with `note.sync-conflict-20260514.md`;
      scan skips it but keeps the canonical `note.md`.
- [ ] `test_scan_does_not_follow_symlinks` — fixture with a symlink dir; scan terminates
      and does not include symlinked notes.
- [ ] `test_scan_partial_success_on_bad_yaml` — fixture with one malformed and one valid
      note; result is `Success(list)` containing only the valid note; WARNING logged.
- [ ] `test_detect_changes_added_only` — empty DB, one note on disk → `added=1, modified=0,
      deleted=0, moved=0`.
- [ ] `test_detect_changes_modified` — pre-seed DB with `(path, hash_old)`; on disk same
      path with `hash_new` → `modified=1`, others zero.
- [ ] `test_detect_changes_deleted` — pre-seed DB with `(path, hash)`; disk empty →
      `deleted=[path]`, `added=[]`.
- [ ] `test_detect_changes_moved_when_hash_matches` — DB has `(inbox/foo.md, hashA)`;
      disk has `Projects/X/foo.md` with `hashA`; result has `moved=[("inbox/foo.md",
      <new_entry>)]`, `added=[]`, `deleted=[]`.
- [ ] `test_detect_changes_does_not_collapse_ambiguous_move` — two added entries have the
      same hash as one deleted; deleted stays in `deleted`, both added stay in `added`,
      `moved=[]`.
- [ ] Run: `uv run pytest tests/test_vault/test_indexer.py -v`

**Status**: [ ] pending

---

### Phase 7 — End-to-end smoke
**Goal**: Prove the layer behaves correctly when its pieces are composed the way Phase 1
will compose them.

**Steps**:
1. Create `tests/test_vault/test_smoke.py`. This test is the closest stand-in for what
   `pipelines/capture.py` will do in Phase 1, but kept inside the vault test tree because
   no pipeline exists yet.
2. Test flow (single test function):
   1. Set up a tmp vault, init DB at `tmp_path / "kb.db"`, run `storage.db.init_db(...)`.
   2. Write a new note via `writer.write_note(path, content, meta, source="ai")` →
      assert `Success(WriteOutcome)`.
   3. Call `storage.documents.upsert(outcome)` → assert `Success(rowid)`.
   4. `storage.documents.get_by_path(outcome.vault_path)` → row matches outcome.
   5. Call `indexer.scan_vault(root)` → `Success([entry])`, single entry with same
      `content_hash`.
   6. Call `indexer.detect_changes(current=[entry], db_path=...)` → empty summary
      (nothing changed since step 3).
   7. Modify the note via `writer.write_note(same path, new content, ..., source="ai")` →
      Success.
   8. Scan again, detect_changes WITHOUT re-upserting → `modified=[new_entry]`.
   9. Apply the change via `storage.documents.upsert(new_outcome)`; detect_changes →
      empty summary.
   10. `writer.move_note(path, new_path, "ai")` → Success; `storage.documents.rename(...)`;
       scan + detect_changes → empty.
   11. Lock the note: write via `source="human"`; subsequent `source="ai"` write →
       `Failure(recoverable=False)`.
3. No new production files in this phase — only the test.

**Files to modify**:
- `tests/test_vault/test_smoke.py` — new.

**Test criteria**:
- [ ] `test_vault_layer_end_to_end` — single function covering all eleven steps above.
      Mark with `@pytest.mark.smoke` so it can be skipped on machines without a working
      DB layer.
- [ ] Run: `uv run pytest tests/test_vault/test_smoke.py -v`
- [ ] Run the full suite to ensure no regressions: `uv run pytest -m "not integration"`

**Status**: [ ] pending

---

## Open Questions

These have **recommended defaults** baked into the phases above. Override during code
review if needed.

| ID | Question | Default in plan | Override impact |
|---|---|---|---|
| OQ-V4 | PyYAML 1.1 bool quirk handling | `field_validator(mode="before")` coerces `bool → str` for known string fields (Phase 2 step 3). Logs at debug. | Switching to a pre-parse normalizer in `parse` instead of validators centralizes the rule but pollutes the parse function. |
| OQ-V5 | Strip trailing newline before hashing/writing | Yes — `body.rstrip("\n")` in both reader and writer (Phase 3 step 3, Phase 4 step 4). | If we keep the trailing newline, indexer reports phantom `modified` after every dumps round-trip. |
| OQ-V6 | NFC normalization on vault paths | On — `unicodedata.normalize("NFC", ...)` inside `_to_vault_path` (Phase 4 step 6). | Off saves microseconds; on prevents ghost-duplicate rows for macOS-authored accented filenames. |
| OQ-V7 | fsync the parent directory after `os.replace` | Off (Phase 4 step 8). | On only matters for catastrophic OS crash during write; not justified for our threat model. |

## Out of Scope

- **Watcher / scheduler** for automatic vault scans — Phase 9 (`scheduler/runner.py`)
  triggers `scan_vault` + `detect_changes` on a cadence. The vault layer just exposes the
  functions.
- **Embeddings** — Phase 3 owns `sentence-transformers`. The indexer must not call it.
- **Per-section authorship** — `updated_by_human` is whole-note (STATE.md DECISION-002,
  TD-V2). HTML-comment / edits-table designs are explicitly Phase 7+.
- **Frontmatter `doc_id`** — stable identity across edit-and-move (STATE.md Q-001,
  TD-V4) is deferred. Phase 0 lives with the "edited + moved simultaneously" blind spot.
- **File-level locking for concurrent writers** — Phase 4 (MCP daemon) addresses
  read-then-write race conditions on `updated_by_human`. Phase 0 assumes single-shot CLI
  (TD-V3).
- **Symlink-following indexer** — first cut skips them entirely. Re-enabling requires
  loop detection and is deferred (TD-V6).
- **Title derivation from H1 heading** — the reference project's `inferTitleFromContent`
  is a Phase 1 capture concern, not vault. Phase 5's `upsert` falls back to filename stem
  for now.
- **Pipeline that wires writer + upsert** — that is Phase 1 (Capture). The smoke test in
  Phase 7 stands in temporarily.
