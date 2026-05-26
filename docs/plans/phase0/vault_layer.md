# Plan: vault_layer
_Last updated: 2026-05-15 (attachment handling + full layout alignment)_
_Status: [x] done_

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
│                              │ move_attachment(src, dst) →      │    │
│                              │   Result[Path]  (binary, no FM)  │    │
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
    │   actor="ai")     │                       │                │
    │──────────────────▶│                       │                │
    │                   │ exists? parse(path)   │                │
    │                   │──────────────────────▶│                │
    │                   │ ◀─(NoteMetadata, body)│                │
    │                   │                                        │
    │                   │ existing.updated_by_human + actor=ai?  │
    │ ◀─Failure(rec=F)──│   yes ────────────────                 │
    │                   │   no → merge meta                      │
    │                   │   (keep created; bump updated;         │
    │                   │    set updated_by_human ← actor)       │
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
        actor=ai    │               │  actor=human (any write)
        re-write OK │               ▼
                    │           ┌───────────────────┐
                    │           │  human-owned      │
                    │           │  updated_by_      │
                    │           │   human = True    │
                    │           └─┬───────────────┬─┘
                    │             │ actor=ai      │ actor=human
                    │             ▼               ▼
                    │       Failure(rec=False)   stays human
                    ▼
                stays ai-owned
```

**attachment capture flow (non-md drop — Phase 1 pipeline, shown for context)**

```
  PDF/DOCX dropped in inbox/
            │
            ▼
  pipelines/capture.py  (Phase 1, future)
            │
            ├─▶ AI summarizes the source → sibling .md note
            │     writer.write_note(inbox/Report.md, summary,
            │       metadata(source="pdf",
            │                source_file="attachment/Report.pdf"),
            │       actor="ai")
            │     body carries Obsidian [[Report.pdf]] wikilink
            │
            └─▶ writer.move_attachment(inbox/Report.pdf,
                                       attachment/Report.pdf)
                  → Result[Path]   (binary move, no frontmatter,
                                    no updated_by_human gate)

  Result: .md sibling stays in inbox (classified later in Phase 2);
          source binary lives in attachment/, reference-only.
  indexer skips attachment/ — non-.md files are filtered by extension.
```

## Approach

Build the five vault modules **bottom-up** so each phase has only the dependencies already in
place: paths → frontmatter → reader → writer → indexer. Add one storage-layer sibling
(`storage/documents.py`) between writer and indexer to host the SQLite mirror upsert, so the
writer stays FS-only and pipelines glue the two layers with one extra call. Every phase ships
its tests in the same commit; nothing depends on a future phase to be verifiable.

Four design decisions confirmed before drafting:

1. **paths.py** exposes only **parametrized** helpers. Static folder roots stay on
   `VaultConfig` properties already in `core/config.py`. No duplication.
2. **writer.py** is **FS-only**. It returns `WriteOutcome` carrying everything needed to
   upsert SQLite. Callers (pipelines) call `storage.documents.upsert(outcome)` as the next
   step. The "tiny addition" — a one-line helper that pipelines invoke — lives in
   `storage/documents.py`, not in the vault layer.
3. **indexer.py** returns a `ChangeSummary` with **four** sets: `added`, `modified`,
   `deleted`, `moved`. Move detection is mandated by STATE.md DECISION-001 and saves Phase 1
   from re-implementing it.
4. **attachments are not `.md` notes.** Non-md source files (PDF, DOCX) live in `attachment/`
   and are moved there by a dedicated `move_attachment` (Phase 8) — a binary-safe move with
   no frontmatter read and no `updated_by_human` gate. They are never indexed; the `.md`
   sibling note that summarizes them is the indexed/classified artifact.

**Layout alignment** (from `docs/obsidian_vault_layout.md`): briefings nest under a year
folder (`Briefings/2026/04_25.md`); classified notes land in `Projects/<name>/materials/`
and `Domain/<name>/notes/` subfolders; the `attachment/` folder is new. Phase 1 paths and
Phase 2 metadata are adjusted for this; Phase 8 adds attachment support as a self-contained
unit.

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
3. Implement eight functions, each returning `pathlib.Path`. Each calls
   `mkdir(parents=True, exist_ok=True)` on the directory the caller will write into:
   - `project_dir(name: str) -> Path`            → `projects_path / name`
     (the project folder itself — home of `project_index.md`)
   - `project_materials(name: str) -> Path`      → `projects_path / name / "materials"`
     (where classified notes land — see `docs/obsidian_vault_layout.md`)
   - `domain_dir(name: str) -> Path`             → `domain_path / name`
     (the domain folder itself — home of `domain_index.md`, `context.yaml`)
   - `domain_notes(name: str) -> Path`           → `domain_path / name / "notes"`
     (where domain-knowledge notes land)
   - `documentation(project: str) -> Path`       → `documentation_path / f"{project}.md"`
     (mkdir the parent, not the file)
   - `briefings_today() -> Path`                 → `briefings_path / str(year) / f"{MM}_{DD}.md"`
     for today — e.g. `Briefings/2026/04_25.md`. mkdir the year directory.
   - `briefings_for(d: date) -> Path`            → `briefings_path / str(d.year) /
     f"{d.month:02d}_{d.day:02d}.md"` — e.g. `briefings_for(date(2026, 4, 25))` →
     `Briefings/2026/04_25.md`.
   - `synthesis_week(d: date) -> Path`           → `synthesis_path / f"{iso_week_filename(d)}.md"`
     where `iso_week_filename(date(2026, 4, 25))` returns `"2026-W17.md"` via
     `d.isocalendar()`.
4. Briefings filename format is `Briefings/YYYY/MM_DD.md` (year folder + zero-padded
   month/day), matching `docs/obsidian_vault_layout.md`. This **overrides** the research
   file's flat-ISO recommendation (`2026-04-25.md`) — the layout doc is the source of truth.
   Synthesis keeps the ISO-week filename; the layout doc does not specify a synthesis format.
5. Add a module docstring documenting: no static folder helpers (use
   `CONFIG.main.vault.inbox_path`, `.attachment_path`, etc. directly); functions create
   dirs but do not write files.

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
- [ ] `test_project_materials_returns_materials_subfolder` — `project_materials("X")`
      returns `<root>/Projects/X/materials` and the directory exists.
- [ ] `test_domain_dir_returns_subfolder_of_domain` — `domain_dir("Movies")` returns
      `<root>/Domain/Movies` and the directory exists.
- [ ] `test_domain_notes_returns_notes_subfolder` — `domain_notes("Movies")` returns
      `<root>/Domain/Movies/notes` and the directory exists.
- [ ] `test_documentation_returns_md_path_with_parent_created` — `documentation("Y")`
      returns `<root>/Documentation/Y.md`, the file does NOT exist (no write), the parent
      directory does.
- [ ] `test_briefings_today_year_nested_format` — `briefings_today()` ends with
      `<year>/<MM>_<DD>.md` and the year directory exists.
- [ ] `test_briefings_for_specific_date` — `briefings_for(date(2026, 4, 25))` ends with
      `2026/04_25.md`.
- [ ] `test_synthesis_week_iso_week_format` — `synthesis_week(date(2026, 4, 25))` ends
      with `2026-W17.md`.
- [ ] `test_helpers_idempotent` — calling each helper twice does not raise and the
      directory still exists.
- [x] Run: `uv run pytest tests/test_vault/test_paths.py -v`

**Completed**: 2026-05-15
**Notes**: Required `uv pip install -e .` after creating vault/ — setuptools editable finder caches discovered packages at install time. Pre-existing mypy `CONFIG` attr-defined error (same pattern as storage/db.py) — not introduced by this phase. All 9 criteria pass.

**Status**: [x] done

---

### Phase 2 — frontmatter.py
**Goal**: A typed wrapper around `python-frontmatter` that other modules use instead of
the library directly.

# RESOLVED: What are source, status, extra, model_config and do all fields get written back?
# source: a string recording where the note came from — e.g. "email", "web", "pdf", "youtube".
#   Not the same as WriteActor (the actor making a write). This is a note property, like a URL or origin label.
# status: lifecycle state string — e.g. "active", "archived", "review". AI or user sets it; pipelines filter on it.
# extra: catch-all dict for any frontmatter key not explicitly on NoteMetadata. Preserves round-trip fidelity:
#   if a user wrote `custom_field: 5` in their note, `extra` holds it so dumps() writes it back unchanged.
# model_config = {"extra": "ignore"}: Pydantic instruction to silently ignore unknown keys at model
#   construction time. We pre-extract unknowns into extra ourselves in parse(), so Pydantic never sees them.
# Do all fields get written back? Yes. dumps() serializes all non-None known fields plus everything in extra
#   back into the YAML frontmatter block. extra keys are merged directly (no nesting).

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
   - `source: str | None = None`   ← origin TYPE of the note (email, web, pdf, youtube, etc.)
   - `source_file: str | None = None`   ← POSIX relative path to the source binary in
     `attachment/` when this note is a sibling summary of a non-md drop (e.g.
     `"attachment/Q2 Report.pdf"`). `None` for notes that have no attachment.
   - `status: str | None = None`   ← lifecycle state (active, archived, review, etc.)
   - `extra: dict[str, Any] = Field(default_factory=dict)`   ← unknown keys preserved here
   - `model_config = {"extra": "ignore"}` — unknown keys are pre-extracted into `extra` by
     `parse`, never passed to the model.
3. Add a `field_validator(mode="before")` on every `str | None` field (`type`, `project`,
   `domain`, `summary`, `source`, `source_file`, `status`) that coerces a `bool` to its
   lowercase string form (`True` → `"yes"`, `False` → `"no"`). This neutralises the **PyYAML 1.1 bool
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
- [ ] `test_vietnamese_content_round_trips_without_escaping` — note with Vietnamese text
      in `summary` (`"Phân loại dự án"`) and `status` (`"đang hoạt động"`); parse → dumps →
      parse; values are identical strings, no `\uXXXX` escaping in the emitted YAML.
- [ ] `test_source_file_field_round_trips` — note with `source_file:
      attachment/Q2 Report.pdf`; parse → `metadata.source_file` equals that string; dumps →
      parse preserves it. A note without the key parses to `source_file == None`.
- [x] Run: `uv run pytest tests/test_vault/test_frontmatter.py -v`

**Completed**: 2026-05-15
**Notes**: Custom `_BlockDumper` (yaml.Dumper subclass) forces block-style list output for Obsidian compat. `dumps()` rebuilds YAML header directly rather than delegating to `python-frontmatter`'s internal dumper — needed to control `allow_unicode=True` and block-list style together. PyYAML bool coercion handled via `field_validator(mode="before")` on all `str | None` fields.

**Status**: [x] done

---

### Phase 3 — reader.py
**Goal**: One function that loads a note from disk and returns a hashed, typed `Note`.

# RESOLVED: What is the hash and its purpose?
# content_hash = SHA-256 hex digest of the note body (body only — not the frontmatter).
# Purpose: change detection fingerprint. When indexer.detect_changes() runs, it compares each
#   note's current hash against the stored hash in `documents`. If they differ → the body was
#   modified → classify it as `modified`. If they match but the path changed → it was moved.
#   Hashing body-only (not frontmatter) means a pure AI metadata update (e.g. bumping `confidence`)
#   does not retrigger re-embedding identical content in Phase 3. The hash is the indexer's
#   source of truth; it is also stored in WriteOutcome so upsert() can persist it to SQLite.

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
- [x] Run: `uv run pytest tests/test_vault/test_reader.py -v`

**Completed**: 2026-05-15
**Notes**: `read_note` uses `match/case` on the Result from `vault.frontmatter.parse` — propagates Failure unchanged. `Note` is a frozen dataclass. `rstrip("\n")` applied before hashing and stored as `content` so writer and reader use identical normalisation.

**Status**: [x] done

---

### Phase 4 — writer.py (load-bearing)
**Goal**: Atomic, idempotent vault writes with the `updated_by_human` safety gate.

# RESOLVED: Please draw a diagram of this Phase 4 to help me understand the logic and how things interact
# Diagrams below show write_note decision flow and move_note flow.

**write_note decision flow**

```
write_note(path, content, metadata, source)
         │
         ▼
    body = content.rstrip("\n")   ← normalize trailing newline
         │
    path.exists()?
         │
    ┌────┴────────────────────────────────────────────────────────────┐
    │ YES                                                             │ NO
    │                                                                 │
    │  reader.read_note(path) ──▶ existing Note                       │  seed:
    │  ◀── Failure? propagate unchanged                               │  created = metadata.created
    │                                                                 │            or date.today()
    │  existing.metadata.updated_by_human == True                     │
    │  AND actor == "ai"?                                             │
    │  ┌── YES ──────┐     ┌── NO ─────────────────────────────────┐  │
    │  │             │     │                                       │  │
    │  │  return     │     │  preserve: created ← existing.created │  │
    │  │  Failure    │     │  set:      updated ← datetime.now()   │  │
    │  │  rec=False  │     │            updated_by_human           │  │
    │  │  "note      │     │              ← (actor == "human")     │  │
    │  │   locked"   │     │  keep: extra ← existing.extra         │  │
    │  └─────────────┘     │            if caller didn't supply    │  │
    │                      └───────────────────────────────────────┘  │
    └─────────────────────────────────────────────────────────────────┘
         │
         ▼  (merge complete)
    frontmatter.dumps(merged_metadata, body) → rendered string
         │
         ▼
    tmp = path.parent / f".tmp_{uuid4().hex}.md"   ← dot-prefix → indexer skips
    write rendered to tmp
    flush + fsync
    os.replace(tmp, path)  ← atomic on POSIX/Windows same-fs
         │
    Exception? ──▶ tmp.unlink(missing_ok=True) ──▶ return Failure
         │
         ▼
    content_hash = sha256(body.encode()).hexdigest()
         │
         ▼
    return Success(WriteOutcome(vault_path, absolute_path, content_hash, metadata))
```

**move_note decision flow**

# RESOLVED: What is Errno 18 / same filesystem in the diagram?
# "Same filesystem" = src and dst are on the same disk volume / mount point. NOT "same location."
#   Example: vault at ~/vault/, tmp at ~/vault/.tmp_xyz.md → SAME filesystem → os.replace works.
#   Example: vault on a OneDrive network mount, dst on local disk → DIFFERENT filesystem → Errno 18.
#
# Errno 18 = EXDEV = "Cross-device link" — what os.replace() raises when src and dst are on
#   different volumes. The OS cannot do an atomic rename across volumes (different inode spaces).
#
# Why "same filesystem fast path" works:
#   os.replace(src, dst) on the SAME filesystem = atomic directory-entry rename. Zero bytes are
#   copied. The OS just moves the file's pointer in the directory tree. Instant and atomic.
#
# Cross-filesystem fallback (what the diagram shows for the NO branch):
#   Cannot rename across volumes, so we copy instead:
#   1. Write content to .tmp_* NEXT TO dst (on the destination volume).
#   2. fsync that tmp file (make it durable on dst's filesystem).
#   3. os.replace(tmp, dst) — now SAME filesystem → atomic rename. dst has the content.
#   4. src.unlink() — delete the original on the source filesystem.
#   Net result: note at dst, original gone. Two operations, not single-atomic, but correct.

# RESOLVED: Check current design for human-created notes + frontmatter-only AI edits.
# NEW NOTE SCENARIO — already handled correctly by the current design:
#   Human creates note in Obsidian (with or without frontmatter) →
#   updated_by_human defaults to False → AI can classify, add frontmatter, move the note. ✓
#   A brand-new note with no frontmatter: NoteMetadata() → updated_by_human=False → gate passes.
#   AI writing with actor="ai" → gate check: False → allowed → AI stamps updated_by_human=False.
#
# HOW updated_by_human GETS SET TO TRUE in Phase 0/1:
#   ONLY via explicit write_note(actor="human") or move_note(actor="human") CLI calls.
#   Obsidian edits to the raw file do NOT auto-set the flag — there is no watcher in Phase 0/1.
#   So: human edits note body in Obsidian → updated_by_human stays False → AI can still update
#   frontmatter on the next pipeline run. This IS the "collaborative" behavior the scenario asks for.
#
# PHASE 4+ DESIGN QUESTION (OQ-V8 added to Open Questions):
#   When the Phase 9 watcher detects a human file edit and decides to set updated_by_human=True,
#   should the whole note be locked (DECISION-002 as written), or should AI still be able to update
#   frontmatter if the body is unchanged? This is a Phase 4/9 question — not Phase 0.
#   No code change needed now. The vault layer already supports the Phase 0/1 scenario correctly.
```
move_note(src, dst, actor)
         │
         ▼
    reader.read_note(src) ──▶ current Note
    ◀── Failure? propagate
         │
    current.metadata.updated_by_human AND actor == "ai"?
    ┌── YES ──┐       ┌── NO ────────────────────────────────────────────────┐
    │ Failure │       │                                                      │
    │ rec=F   │       │  dst.parent.mkdir(parents=True, exist_ok=True)       │
    └─────────┘       │  bump updated; set updated_by_human ← (actor==      │
                      │  "human"); keep existing body unchanged              │
                      │                                                      │
                      │  same filesystem?                                    │
                      │  ┌── YES ─────────────────────┐  ┌── NO (Errno 18)─┐│
                      │  │ os.replace(src, dst)        │  │ copy to .tmp_*  ││
                      │  │ (atomic location change)    │  │ next to dst     ││
                      │  │ then re-write dst with      │  │ fsync           ││
                      │  │ merged meta via atomic-     │  │ os.replace(tmp, ││
                      │  │ write recipe                │  │   dst)          ││
                      │  └─────────────┬───────────────┘  │ src.unlink()   ││
                      │                │                   └───────┬────────┘│
                      │                └──────────────────────────┘         │
                      │                                                      │
                      │  return Success(WriteOutcome with dst vault_path)    │
                      └──────────────────────────────────────────────────────┘
```
# RESOLVED: Does "user-triggered CLI command" require the human to run it manually, or is it auto-triggered?
# Phase 0/1: MANUAL ONLY. actor="human" is passed by the caller (a CLI command the user runs).
#   The vault layer has no watcher — it does not detect Obsidian edits. There is no auto-trigger.
#   In Phase 1, the user runs `kms capture <file>` → pipeline calls write_note(actor="ai").
#   A future `kms correct <file>` command would call write_note(actor="human") to lock the note.
#
# Phase 9+ (scheduler/watcher): the watcher detects file changes on disk and can trigger pipelines.
#   At that point, the watcher decides whether to use actor="ai" or actor="human" based on
#   whether it detected a human-edit pattern. That is a Phase 9 design question.
#
# Short answer: in Phase 0/1, the human never needs to run a CLI command after editing in Obsidian.
#   The flag is only set when a CLI command explicitly asks for actor="human".
#   Obsidian edits do NOT auto-trigger anything or auto-set updated_by_human in Phase 0/1.
# RESOLVED: What does WriteActor do, and what does 'ai' or 'human' mean?
# WriteActor = Literal["ai", "human"] is a type that signals WHO is making THIS EDIT — not who
#   created the note originally. The caller passes it explicitly on every write_note() or move_note() call.
#   "ai":    a pipeline is making this change (classifier filed the note, AI summarized it, etc.)
#   "human": a user-triggered CLI command is making this change (e.g. `kms move`, `kms correct`)
# It controls two things:
#   1. Whether to respect the updated_by_human lock: AI writes check the lock and abort if set.
#      Human writes bypass the lock and always succeed.
#   2. What value to stamp on updated_by_human in the written frontmatter:
#      actor="ai"    → updated_by_human = False (note stays AI-owned)
#      actor="human" → updated_by_human = True  (note becomes human-owned, future AI writes blocked)

# RESOLVED: What does "if existing.metadata.updated_by_human and actor == 'ai'" mean?
# This is the safety gate — the core trust mechanism of the whole vault layer.
# updated_by_human=True means: "a human has edited this note and marked it as theirs."
# actor="ai" means: "a pipeline is trying to overwrite it."
# When both are true: the AI is trying to overwrite human work. Block it. Return Failure(recoverable=False).
# recoverable=False because no retry will fix this — only a human decision (writing actor="human"
#   or clearing the flag) unlocks the note. The AI must surface a conflict, not silently succeed.

# RESOLVED: What is the purpose of move_note? Why no routing or confidence?
# move_note() is a low-level atomic filesystem operation: move a .md file from src to dst, atomically,
#   with the updated_by_human safety check applied. It does NOT decide where to move the note.
# The routing decision lives in pipelines/classify.py (Phase 2):
#   1. LLM classifies the note → label + confidence score
#   2. Pipeline reads thresholds from config → decides target folder (Projects/X, Domain/Y, etc.)
#   3. Pipeline calls vault.writer.move_note(src=inbox/foo.md, dst=projects/X/foo.md, actor="ai")
# The vault layer is pure mechanics: it does not know what the classification was or why.
# vault/ = HOW to move safely. pipelines/ = WHERE and WHEN to move.

# RESOLVED: Vietnamese filenames — is NFC enough? Do other mechanisms need to change?
# NFC covers the PRIMARY RISK: Vietnamese filename-path comparison in SQLite.
#   Vietnamese uses tonal diacritics (ă, â, đ, ê, ô, ơ, ư + combining tone marks).
#   macOS stores filenames in NFD (decomposed) internally. Python reads them as NFD strings.
#   NFC normalization in _to_vault_path() before storing vault_path in SQLite ensures that
#   both "write then index" and "scan then compare" produce the same string. ✓
#
# FILE CONTENT (Vietnamese text inside notes): no changes needed.
#   File write uses open(..., encoding="utf-8") → all Vietnamese characters are preserved. ✓
#   content_hash uses body.encode("utf-8") → correct byte representation for Vietnamese. ✓
#   yaml.safe_dump (via python-frontmatter) uses allow_unicode=True by default → Vietnamese
#     text in frontmatter values (summary, status, etc.) is written as UTF-8, not escaped. ✓
#
# SQLITE: stores TEXT as UTF-8 natively. No changes needed for Vietnamese text or paths. ✓
#
# EXISTING CODEBASE: no other mechanism needs to change for Phase 0/1.
#   storage/db.py, audit_log.py, core/config.py: all handle UTF-8 strings correctly already.
#   The only place that must normalize is _to_vault_path() in vault/writer.py (Phase 4 step 6)
#   and scan_vault() in vault/indexer.py where VaultEntry.vault_path is computed.
#
# ADD TO PHASE 2 TEST CRITERIA: test that Vietnamese text in frontmatter values round-trips
#   correctly through parse() → dumps() without escaping or corruption.
# RESOLVED: What does NFC mean?
# NFC = Unicode Normalization Form C (Canonical Decomposition, followed by Canonical Composition).
# On macOS (HFS+/APFS), the filesystem sometimes stores filenames with characters in NFD form
#   (Decomposed) — e.g. the letter "é" stored as two codepoints: "e" + combining accent ́.
#   When Python reads the path, it may return NFC ("é" as one codepoint: U+00E9).
# This inconsistency means the same filename can produce two different Python strings depending
#   on how it was read, making vault_path strings not match between SQLite and a fresh scan →
#   indexer reports a spurious "deleted + added" for the same note.
# Fix: always call unicodedata.normalize("NFC", str(path)) before storing or comparing vault_path.
#   Cheap insurance for macOS vaults with accented filenames.

**Steps**:
1. Create `vault/writer.py`.
2. Define `WriteOutcome` as `@dataclass(frozen=True)` with: `vault_path: str` (POSIX,
   relative to vault root), `absolute_path: Path`, `content_hash: str`,
   `metadata: NoteMetadata`. This is what callers feed to
   `storage.documents.upsert` in Phase 5.
3. Define `WriteActor = Literal["ai", "human"]`. See RESOLVED annotation above for full semantics.
4. `def write_note(path: Path, content: str, metadata: NoteMetadata, actor: WriteActor)
   -> Result[WriteOutcome]`:
   - `body = content.rstrip("\n")` (one canonical newline policy — matches reader).
   - If `path.exists()`:
     - `vault.reader.read_note(path)` to get existing `Note`. On `Failure`, propagate.
     - If `existing.metadata.updated_by_human` and `actor == "ai"`:
       return `Failure(error="note locked by human edit", recoverable=False,
       context={"path": str(path), "vault_path": _to_vault_path(path)})`.
     - Preserve `existing.metadata.created` (don't let AI overwrite the original date).
   - Else, treat `existing` as None and seed `created = metadata.created or
     date.today()`.
   - Build the merged metadata:
     - `created` per above
     - `updated = datetime.now(timezone.utc)`
     - `updated_by_human = (actor == "human")`
     - **Option B field merge** — for each known field, the caller's value wins if
       explicitly supplied; otherwise fall back to the existing value so human-set
       fields survive an AI write that doesn't touch them:
       - `Optional[T]` fields (`type`, `project`, `domain`, `summary`, `source`,
         `source_file`, `status`, `confidence`): use caller value if not `None`,
         else use `existing.metadata.<field>` (or `None` if no existing note).
       - `tags` (`list[str]`): use caller value if non-empty, else use
         `existing.metadata.tags` (or `[]` if no existing note).
       - Note: a caller that wants to **explicitly clear** a known field must pass a
         sentinel value (e.g. `tags=["__clear__"]` → pipeline strips sentinel and
         writes `[]`). This is a Phase 1 pipeline concern, not a vault concern.
     - Preserve `extra` from existing if caller didn't provide one
   - `rendered = vault.frontmatter.dumps(merged_metadata, body)` — this assigns the return value
     to the variable `rendered` used in the atomic write below.
   - Atomic write:
   # RESOLVED: Where does 'rendered' in f.write(rendered) come from?
   # rendered = vault.frontmatter.dumps(merged_metadata, body) — the line directly above.
   # dumps() returns a complete string: YAML frontmatter block + body, ready to write to disk.
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
5. `def move_note(src: Path, dst: Path, actor: WriteActor) -> Result[WriteOutcome]`:
   - `vault.reader.read_note(src)` to get current note. Propagate `Failure`.
   - If `current.metadata.updated_by_human` and `actor == "ai"`: same `Failure` as write.
   - `dst.parent.mkdir(parents=True, exist_ok=True)`.
   - Bump `updated`, set `updated_by_human ← (actor == "human")`.
   # RESOLVED: Can os.replace move a file just by replacing its path?
   # Yes. os.replace(src, dst) is the Python wrapper for the POSIX rename() syscall.
   # On the SAME filesystem, rename() is a single atomic OS operation that updates the file's
   #   directory entry — it changes WHERE the file appears in the directory tree, not the file data.
   # No bytes are copied. The file's inode (the actual data on disk) stays exactly where it is.
   # The OS just moves the pointer: "this filename now refers to this inode at this new location."
   # This is why it's instant (no I/O proportional to file size) and atomic (no partial state).
   # The name "replace" comes from: if dst already exists, it is atomically replaced by src.
   - **Same-filesystem fast path**: `os.replace(src, dst)` atomically, then re-write `dst`
     with merged metadata via the atomic-write recipe above. The two-step is needed because
     `os.replace` cannot update content; only the location.
   - **Cross-filesystem fallback** (`OSError: [Errno 18]`): write the rendered content to a
     `.tmp_*` next to `dst`, fsync, `os.replace(tmp, dst)`, then `src.unlink()`.
   - Return `Success(WriteOutcome(...))` with `vault_path` reflecting the destination.
6. Private helper `_to_vault_path(absolute: Path) -> str` — computes
   `absolute.relative_to(CONFIG.main.vault.root).as_posix()` then applies
   `unicodedata.normalize("NFC", ...)` (OQ-V6 default ON; prevents ghost-duplicate rows
   for macOS-authored accented filenames — see RESOLVED NFC annotation above).
7. **Do not** call any `storage/` function. Writer is FS-only.
8. **Do not** fsync the parent directory (OQ-V7 default OFF — developer-grade durability).

**Files to modify**:
- `vault/writer.py` — new.
- `tests/test_vault/test_writer.py` — new tests.

**Test criteria**:
- [ ] `test_write_new_note_creates_file_with_frontmatter` — write to a path that doesn't
      exist; `path.exists()`, content parses back to the same metadata (minus
      `updated` which is server-set), body matches.
- [ ] `test_write_sets_updated_by_human_per_actor` — write with `actor="human"` →
      `updated_by_human=True` in the file; write with `actor="ai"` → False.
- [ ] `test_write_ai_blocked_when_updated_by_human_true` — pre-create file with
      `updated_by_human: true`; attempt `write_note(..., actor="ai")` returns
      `Failure(recoverable=False)`, file content unchanged byte-for-byte.
- [ ] `test_write_human_succeeds_over_updated_by_human` — same setup; `actor="human"`
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
- [ ] `test_move_note_changes_location_and_updates_metadata` — `move_note(a, b, actor="ai")`
      with non-locked `a`; `a` gone, `b` exists with bumped `updated`, body intact.
- [ ] `test_move_note_blocked_when_locked` — `a` locked; `move_note(a, b, "ai")` →
      `Failure`, both files in original state.
- [ ] `test_unicode_normalization_on_vault_path` — file at name containing decomposed
      Unicode (NFD); `outcome.vault_path` is NFC.
- [x] Run: `uv run pytest tests/test_vault/test_writer.py -v`

**Completed**: 2026-05-15
**Notes**: Option B merge implemented in `_merge_metadata` helper. `_atomic_write` extracted as private helper used by both `write_note` and the same-filesystem branch of `move_note`. File-handle variable named `fh` (not `f`) to avoid shadowing the `Failure() as f` match-case binding — mypy flags this. Pre-existing mypy CONFIG `__getattr__`→`object` error unchanged from storage/db.py pattern.

**Status**: [x] done

---

### Phase 5 — storage/documents.py (SQLite mirror)
**Goal**: A small storage module that pipelines call after every `write_note` to keep the
`documents` table in sync with the FS.

# RESOLVED: We already have storage/schema.sql that creates a documents table. Why is this file needed?
# schema.sql is DDL (Data Definition Language): it defines the TABLE STRUCTURE — columns, types,
#   constraints, triggers. It is a blueprint. Running schema.sql creates an empty table.
# storage/documents.py is the DAL (Data Access Layer): it contains the PYTHON CODE that reads
#   and writes rows in that table — upsert(), get_by_path(), all_paths(), etc.
# They serve different purposes and are always separate: one is the SQL schema, the other is the
#   Python interface to it. storage/audit_log.py is the same pattern for the audit_log table.
# Without documents.py, there is no Python code to call — raw sqlite3 SQL would be scattered
#   across pipelines and the indexer, violating the storage-layer abstraction.

# RESOLVED: Please draw a diagram of this Phase 5 to help me understand the logic and how things interact

**storage/documents.py — structure and data flow**

```
┌─────────────────────────────────────────────────────────────────────────┐
│  storage/documents.py   (data access layer for `documents` table)       │
│  No business logic — pure SQL + type conversion only.                   │
│                                                                         │
│  Inputs from vault layer:                                               │
│  WriteOutcome(vault_path, absolute_path, content_hash, metadata)        │
│       │                                                                 │
│       ▼ upsert(outcome, db_path) → Result[int]                          │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │ INSERT OR REPLACE INTO documents                                │    │
│  │   (vault_path, title, content_hash, updated_by_human,          │    │
│  │    created_at, updated_at, note_type, confidence, summary)      │    │
│  │ title: outcome.metadata.extra.get("title") or stem(vault_path) │    │
│  │ Returns rowid                                                   │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                         │
│  get_by_path(vault_path, db_path) → Result[DocumentRow | None]          │
│  ┌─────────────────────────────────┐                                    │
│  │ SELECT * FROM documents         │──▶ DocumentRow (frozen dataclass)  │
│  │ WHERE vault_path = ?            │    or None if not found            │
│  └─────────────────────────────────┘                                    │
│                                                                         │
│  all_paths(db_path) → Result[list[tuple[str, str]]]                     │
│  ┌─────────────────────────────────────────────┐                        │
│  │ SELECT vault_path, content_hash             │──▶ list[(path, hash)] │
│  │ FROM documents                              │    used by indexer     │
│  └─────────────────────────────────────────────┘    for diffing        │
│                                                                         │
│  delete_by_path(vault_path, db_path) → Result[int]                      │
│  ┌─────────────────────────────────────────────┐                        │
│  │ DELETE FROM documents WHERE vault_path = ?  │                        │
│  └─────────────────────────────────────────────┘                        │
│                                                                         │
│  rename(old, new, db_path) → Result[int]                                │
│  ┌─────────────────────────────────────────────┐                        │
│  │ UPDATE documents SET vault_path = ?         │──▶ preserves .id      │
│  │ WHERE vault_path = ?                        │    (DECISION-001)     │
│  └─────────────────────────────────────────────┘                        │
│                                                                         │
│  All functions wrap sqlite3.Error → Failure(recoverable=False,          │
│    context={"vault_path": ..., "op": "upsert"|"get"|...})               │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │ uses get_connection()
                                    ▼
                        ┌──────────────────────────┐
                        │ storage/db.py (exists)   │
                        │ get_connection()         │
                        │ documents table          │
                        │ (schema via schema.sql)  │
                        └──────────────────────────┘

  Who calls what:
  ┌──────────────────────────────────────────────────────────┐
  │ pipelines/capture.py (Phase 1) calls:                    │
  │   writer.write_note(...) → WriteOutcome                  │
  │   documents.upsert(outcome)        ← one extra line      │
  │                                                          │
  │ indexer.detect_changes() calls:                          │
  │   documents.all_paths()            ← for diffing         │
  │   (then caller applies: upsert / delete_by_path / rename)│
  └──────────────────────────────────────────────────────────┘
```

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
- [x] Run: `uv run pytest tests/test_storage/test_documents.py -v`

**Completed**: 2026-05-15
**Notes**: `get_by_path` sets `conn.row_factory = sqlite3.Row` inside the `with` block for named-column access. `upsert` uses `COALESCE(?, datetime('now'))` for `created_at` so first-write timestamp is preserved on subsequent upserts (INSERT OR REPLACE deletes+reinserts the row, so explicit `created_at` must be passed). All 7 criteria pass; 227 total pass.

**Status**: [x] done

---

### Phase 6 — indexer.py
# RESOLVED: Does indexer only cover .md files?
# Yes, intentionally. scan_vault() skips any file that is not .md (case-insensitive).
# Reasons:
#   1. The vault contains attachments (images, PDFs) alongside notes, but they are not
#      knowledge artifacts — they have no frontmatter, no body to classify, no hash to track.
#   2. The `documents` table schema is designed for markdown notes: vault_path, content_hash,
#      frontmatter fields. Indexing a .png or .pdf there makes no sense.
#   3. PDFs/emails/web articles enter the system as INPUT via handlers/ (e.g. PDFHandler),
#      NOT as vault notes. They get captured, summarized, and written as .md into the vault.
#      The original attachment is not indexed — its derived note is.
# If a future phase needs to track attachments (e.g. for de-duplication), that would require
# a separate `attachments` table and a different indexer — not an extension of this one.
**Goal**: Filesystem scan + diff against the SQLite mirror, emitting a four-set
`ChangeSummary`.

# RESOLVED: Please draw a diagram of this Phase 6 to help me understand the logic and how things interact

**scan_vault flow**

```
scan_vault(root=None)
         │
         │ if root is None: lazy-import CONFIG, use CONFIG.main.vault.root
         ▼
    Walk root recursively (os.walk or Path.iterdir)
         │
    For each directory encountered:
    ┌────┴──────────────────────────────────────────────────────┐
    │ skip if dir name in IGNORE_DIRS                           │
    │   {".git", ".obsidian", ".trash", ".stversions",          │
    │    "node_modules", "_assets", "_system"}                  │
    │ skip if dir name starts with "."  (catches .tmp_* dirs)   │
    │ skip if dir is a symlink (loop protection)                 │
    └────┬──────────────────────────────────────────────────────┘
         │
    For each file in allowed directories:
    ┌────┴──────────────────────────────────────────────────────┐
    │ skip if name in IGNORE_FILES {".DS_Store", "Thumbs.db"}   │
    │ skip if name contains ".sync-conflict-"                   │
    │ skip if extension is not ".md" (case-insensitive)         │
    │ skip if file is a symlink                                  │
    └────┬──────────────────────────────────────────────────────┘
         │
    reader.read_note(path)
         │
    ┌────┴────────────────────────────────────────┐
    │ Success → build VaultEntry:                 │
    │   path        = absolute Path              │
    │   vault_path  = NFC(path.relative_to(root))│
    │   content_hash = from Note                 │
    │   metadata     = from Note                 │
    │                                             │
    │ Failure → append to errors[], skip file,   │
    │   continue scan (don't abort full scan)    │
    └────┬────────────────────────────────────────┘
         │ (after all files processed)
    ┌────┴────────────────────────────────────────┐
    │ if errors non-empty:                        │
    │   log WARNING: "N files skipped: [paths]"  │
    └────┬────────────────────────────────────────┘
         │
    return Success([VaultEntry, ...])   ← partial results are useful
```

**detect_changes flow**

```
detect_changes(current: list[VaultEntry], db_path=None)
         │
         ▼
    storage.documents.all_paths(db_path)
    → Result[list[(vault_path, content_hash)]]
    ◀── Failure? propagate
         │
         ▼
    Build lookup maps:
      current_by_path: dict[vault_path → VaultEntry]
      db_by_path:      dict[vault_path → content_hash]
         │
         ▼  First pass — three sets
    ┌──────────────────────────────────────────────────────┐
    │  added_raw  = {e for e in current                    │
    │                if e.vault_path not in db_by_path}    │
    │                                                      │
    │  deleted_raw = {p for p in db_by_path                │
    │                 if p not in current_by_path}         │
    │                                                      │
    │  modified   = {e for e in current                    │
    │                if e.vault_path in db_by_path         │
    │                and e.content_hash !=                 │
    │                    db_by_path[e.vault_path]}         │
    └────────────────────────┬─────────────────────────────┘
                             │
                             ▼  Move detection (DECISION-001)
    ┌──────────────────────────────────────────────────────────┐
    │ For each entry in added_raw:                             │
    │   candidates = [p for p in deleted_raw                  │
    │                 if db_by_path[p] == entry.content_hash] │
    │                                                          │
    │   exactly 1 candidate?                                   │
    │   ┌── YES ────────────────────┐  ┌── NO (0 or 2+) ─────┐│
    │   │ moved.append(            │  │ entry stays in       ││
    │   │   (candidate, entry))    │  │ added_raw            ││
    │   │ added_raw.remove(entry)  │  └──────────────────────┘│
    │   │ deleted_raw.remove(cand) │                          │
    │   └───────────────────────────┘                          │
    └──────────────────────────────────────────────────────────┘
                             │
                             ▼
    log INFO: "Scan complete: X added, Y modified, Z deleted, W moved (total N)"
                             │
                             ▼
    return Success(ChangeSummary(
      added    = list(added_raw),
      modified = list(modified),
      deleted  = list(deleted_raw),
      moved    = list(moved)      ← list[tuple[str, VaultEntry]]
    ))

    NOTE: indexer does NOT write to SQLite.
    The caller (a Phase 1+ pipeline) decides what to apply:
      upsert(outcome) for added/modified
      delete_by_path(path) for deleted
      rename(old, new) for moved
```

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
   - Walk `root` recursively. Skip per the diagram above.
   - For each remaining `.md`, call `vault.reader.read_note(path)`. On `Failure`, append
     the error to a per-call `errors` list, skip the file, continue scanning (do not abort
     a full scan because one note has malformed YAML).
   - Build `VaultEntry` per success; return `Success([...])`. If `errors` is non-empty,
     log a single WARNING with the count and the first three paths, then still return
     `Success` — the scan is informational and partial results are useful.
5. `def detect_changes(current: list[VaultEntry], db_path: Path | None = None) ->
   Result[ChangeSummary]`:
   - `storage.documents.all_paths(db_path)` → propagate `Failure`.
   - First pass: compute `added_raw`, `deleted_raw`, `modified` per diagram.
   - Move detection (DECISION-001): per diagram — only collapse to `moved` when exactly
     one hash match exists to avoid ambiguous multi-copy scenarios.
   - Log INFO summary. Return `Success(ChangeSummary(...))`.
6. The indexer **does not write** to `documents`. It returns a `ChangeSummary`; the caller
   decides what to apply.

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
- [x] Run: `uv run pytest tests/test_vault/test_indexer.py -v`

**Completed**: 2026-05-15
**Notes**: Used stdlib `logging.getLogger(__name__)` instead of structlog so `caplog` can capture the WARNING in tests (structlog's default PrintLogger doesn't route through stdlib in test environments without `setup_logging()`). Bidirectional uniqueness check for move detection: collapse only when exactly 1 deleted AND exactly 1 added share the hash — prevents ambiguous multi-copy collapse. All 11 criteria pass; 276 total pass.

**Status**: [x] done

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
   2. Write a new note via `writer.write_note(path, content, meta, actor="ai")` →
      assert `Success(WriteOutcome)`.
   3. Call `storage.documents.upsert(outcome)` → assert `Success(rowid)`.
   4. `storage.documents.get_by_path(outcome.vault_path)` → row matches outcome.
   5. Call `indexer.scan_vault(root)` → `Success([entry])`, single entry with same
      `content_hash`.
   6. Call `indexer.detect_changes(current=[entry], db_path=...)` → empty summary
      (nothing changed since step 3).
   7. Modify the note via `writer.write_note(same path, new content, ..., actor="ai")` →
      Success.
   8. Scan again, detect_changes WITHOUT re-upserting → `modified=[new_entry]`.
   9. Apply the change via `storage.documents.upsert(new_outcome)`; detect_changes →
      empty summary.
   10. `writer.move_note(path, new_path, actor="ai")` → Success; `storage.documents.rename(...)`;
       scan + detect_changes → empty.
   11. Lock the note: write via `actor="human"`; subsequent `actor="ai"` write →
       `Failure(recoverable=False)`.
3. No new production files in this phase — only the test.

**Files to modify**:
- `tests/test_vault/test_smoke.py` — new.

**Test criteria**:
- [x] `test_vault_layer_end_to_end` — single function covering all eleven steps above.
      Mark with `@pytest.mark.smoke` so it can be skipped on machines without a working
      DB layer.
- [x] Run: `uv run pytest tests/test_vault/test_smoke.py -v`
- [x] Run the full suite to ensure no regressions: `uv run pytest -m "not integration"`

**Completed**: 2026-05-15
**Notes**: No new production files — test-only phase. Smoke test passes immediately because all composition dependencies (phases 1-6) are already implemented and correct. Used `vault_root` fixture (tmp vault + CONFIG monkeypatch) and explicit `db_path=tmp_path/"kb.db"` to avoid any real-vault dependency. 278 total pass.

**Status**: [x] done

---

### Phase 8 — Attachment support
**Goal**: Let the vault layer relocate non-md source files (PDF, DOCX) into a dedicated
`attachment/` folder, so Phase 1 capture can keep the binary as reference-only material
while the `.md` sibling note is the indexed artifact.

**Why a separate phase**: attachments are fundamentally different from notes — binary, no
frontmatter, no `updated_by_human` flag, never indexed. Bundling their move logic into
`write_note`/`move_note` would force those functions to branch on file type. A dedicated
`move_attachment` keeps each function single-purpose.

**move_attachment decision flow**

```
move_attachment(src, dst)
         │
         ▼
    src.exists()?
    ┌── NO ──┐   ┌── YES ──────────────────────────────────────────────┐
    │Failure │   │                                                      │
    │rec=F   │   │  dst.parent.mkdir(parents=True, exist_ok=True)       │
    │"source │   │                                                      │
    │ not    │   │  dst.exists()?                                       │
    │ found" │   │  ┌── YES ──────────────┐  ┌── NO ──────────────────┐ │
    └────────┘   │  │ Failure(rec=False)  │  │  same filesystem?      │ │
                 │  │ "attachment already │  │  ┌─ YES ─┐ ┌─ NO ────┐ │ │
                 │  │  exists at dst" —    │  │  │os.    │ │copy bytes│ │ │
                 │  │  caller picks a      │  │  │replace│ │→.tmp_*   │ │ │
                 │  │  non-colliding name  │  │  │(src,  │ │fsync     │ │ │
                 │  │  (NO silent clobber) │  │  │ dst)  │ │os.replace│ │ │
                 │  └─────────────────────┘  │  │       │ │src.unlink│ │ │
                 │                            │  └───┬───┘ └────┬─────┘ │ │
                 │                            │      └─────┬─────┘      │ │
                 │                            └────────────┼────────────┘ │
                 │                                         │              │
                 │  on any exception: unlink .tmp_*, return Failure        │
                 │                                         │              │
                 │                            return Success(dst)         │
                 └──────────────────────────────────────────────────────┘
```

**Steps**:
1. **`core/config.py`** — add to `VaultConfig` (the class docstring already says "add a new
   folder here + a matching `@property` — nothing else changes"):
   ```python
   attachment_dir: str = "attachment"
   @property
   def attachment_path(self) -> Path: return self.root / self.attachment_dir
   ```
   No `config/config.yaml` change needed — the field has a default; `config.yaml` only
   overrides when a non-default name is wanted.
2. **`vault/writer.py`** — add `def move_attachment(src: Path, dst: Path) -> Result[Path]`:
   - If `not src.exists()`: return `Failure(error="attachment source not found",
     recoverable=False, context={"src": str(src)})`.
   - `dst.parent.mkdir(parents=True, exist_ok=True)`.
   - If `dst.exists()`: return `Failure(error="attachment already exists at dst",
     recoverable=False, context={"dst": str(dst)})`. **Never silently overwrite** — two
     different PDFs can share a filename; the caller picks a non-colliding name and retries.
     (This differs from `move_note`/`write_note`, which intentionally overwrite by path.)
   - **Same-filesystem fast path**: `os.replace(src, dst)` — atomic.
   - **Cross-filesystem fallback** (`OSError: [Errno 18]`): copy bytes to `dst.parent /
     f".tmp_{uuid4().hex}{dst.suffix}"`, `fsync`, `os.replace(tmp, dst)`, then `src.unlink()`.
   - On any exception during the move: `tmp.unlink(missing_ok=True)` (if a tmp was created),
     return `Failure`.
   - Return `Success(dst)` — the final absolute path.
   - **No frontmatter read** (the source is a binary — `reader.read_note` would fail on it).
     **No `updated_by_human` gate** (binary files carry no frontmatter flag).
     **No `WriteOutcome`** — attachments are not in the `documents` index, so there is no
     `content_hash` or metadata to return. `Result[Path]` is the full contract.
3. **Module docstring note in `writer.py`**: `move_attachment` is for non-md binaries only;
   `.md` notes always go through `write_note` / `move_note`.
4. The caller (Phase 1 `pipelines/capture.py`) is responsible for: choosing the `dst` name
   under `CONFIG.main.vault.attachment_path`, NFC-normalizing it for the `.md` note's
   `source_file` field, and writing the `.md` sibling via `write_note`. The vault layer only
   performs the move.

**Files to modify**:
- `core/config.py` — add `attachment_dir` field + `attachment_path` property to `VaultConfig`.
- `vault/writer.py` — add `move_attachment`.
- `tests/test_vault/test_writer.py` — append `move_attachment` tests.
- `tests/test_core/test_config.py` — add `attachment_path` assertion.

**Test criteria**:
- [x] `test_attachment_path_derives_from_root` — `VaultConfig(root=tmp).attachment_path`
      equals `tmp / "attachment"`.
- [x] `test_move_attachment_relocates_binary` — write a fake PDF (random bytes) to
      `inbox/Report.pdf`; `move_attachment` to `attachment/Report.pdf`; src is gone, dst
      exists, byte content is identical.
- [x] `test_move_attachment_creates_dst_parent` — `attachment/` does not exist before the
      call; after, the directory exists and holds the file.
- [x] `test_move_attachment_fails_when_dst_exists` — pre-create `attachment/Report.pdf`;
      `move_attachment(inbox/Report.pdf, attachment/Report.pdf)` returns
      `Failure(recoverable=False)`; both files unchanged byte-for-byte.
- [x] `test_move_attachment_missing_src` — non-existent `src` → `Failure(recoverable=False)`.
- [x] `test_move_attachment_does_not_parse_frontmatter` — move a file whose bytes are NOT
      valid markdown (e.g. `b"\x00\x01\x02not utf8\xff"`); the move still succeeds, proving
      no `reader.read_note` / frontmatter parse happens on the path.
- [x] `test_move_attachment_atomic_no_partial_on_failure` — monkeypatch `os.replace` to
      raise; assert `src` is unchanged and no `.tmp_*` file remains in `attachment/`.
- [x] Run: `uv run pytest tests/test_vault/test_writer.py tests/test_core/test_config.py -v`

**Completed**: 2026-05-15
**Notes**: `attachment_dir: str = "attachment"` field + `attachment_path` property added to `VaultConfig`. `move_attachment` in writer.py: same-filesystem fast path (os.replace) + cross-filesystem fallback (binary copy to .tmp_*, fsync, os.replace, src.unlink). `tmp = None` sentinel after ownership transfer prevents double-unlink in cleanup. No frontmatter read, no updated_by_human gate, Result[Path] return (not WriteOutcome). 285 total pass.

**Status**: [x] done

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
| OQ-V8 | When Phase 9 watcher detects a human body edit and sets `updated_by_human=True`, should AI still be allowed to update frontmatter (metadata-only writes)? DECISION-002 says whole-note lock — no AI writes at all. The user's scenario (Phase 4 COMMENT) suggests frontmatter-only AI edits should remain allowed. | Default: keep DECISION-002 for Phase 0/1 (no watcher, no auto-lock). Revisit when Phase 9 watcher is designed — at that point decide whether to soften the lock to "body-only gate" or keep it whole-note. | Softening requires write_note to compare incoming body vs existing body — implementable but adds complexity to Phase 4 writer. |

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
- **Non-md capture orchestration** — the logic that summarizes a PDF/DOCX, creates the
  sibling `.md` note, picks the `attachment/` destination name, and calls `move_attachment`
  is Phase 1 (`pipelines/capture.py`). Phase 8 only provides the `move_attachment` primitive.
- **`source_file` as a `documents` SQL column** — for now `source_file` lives only in the
  `.md` note's frontmatter. If a pipeline later needs to query "which note summarizes this
  attachment?" from SQL, that is a migration (STATE.md TD-008 pattern), not a Phase 0/8 change.
- **Attachment indexing / de-duplication** — `attachment/` files are never scanned or
  hashed by the indexer. Tracking attachments (e.g. detecting the same PDF dropped twice)
  would need a separate `attachments` table and is out of scope.
- **Video / media handling** — no video files are accepted as drops. YouTube/website
  content enters as a `.md` note containing the link; summarizing that linked content is a
  Phase 1+ handler concern, not a vault-layer one.
