---
name: vault_layer
description: Phase 0 vault layer research — paths, frontmatter, reader, writer, indexer. What to build, how it ties to core/ and storage/, what to borrow from the JS reference, and the design tensions the plan must resolve.
metadata:
  type: project
---

# Research: vault_layer
_Last updated: 2026-05-14_

## Overview

The vault layer is the only code in the project allowed to read or write the Obsidian
filesystem. It is a **thin, well-bounded module set** that wraps `pathlib`, `python-frontmatter`,
and `hashlib` behind project-specific contracts: idempotent writes, the
`updated_by_human` safety gate, content-hash change detection, and a single place to ask
"where does the inbox live?". Every downstream phase (Capture, Classify, Promotion,
Documentation, Briefing) touches the vault exclusively through this layer.

Five files to deliver in Phase 0 (per [docs/roadmap.md](../roadmap.md) and the feature spec
the user provided):

1. [vault/paths.py](../../vault/paths.py) — derived path helpers (one place builds vault paths).
2. [vault/frontmatter.py](../../vault/frontmatter.py) — wraps `python-frontmatter` with a
   typed `NoteMetadata` model.
3. [vault/reader.py](../../vault/reader.py) — `read_note(path) -> Result[Note]` with body hash.
4. [vault/writer.py](../../vault/writer.py) — load-bearing. Atomic upsert + `updated_by_human`
   enforcement.
5. [vault/indexer.py](../../vault/indexer.py) — scan + change detection against the SQLite
   index.

The layer has **no consumers in Phase 0** — Phase 1 (Capture) is its first caller. It must
therefore be testable in isolation against `tmp_path` fixtures, not against the live vault.

## Key Components

**To build (none exist yet — `tests/test_vault/` is an empty directory):**

| File | Role |
|---|---|
| `vault/paths.py` | Stateless helpers that return `pathlib.Path` objects and call `mkdir(parents=True, exist_ok=True)`. Parametrized helpers (`briefings_for(date)`, `project_dir(name)`, `documentation(project)`, `synthesis_week(date)`) are the **only** reason this module exists beyond [VaultConfig properties already in `core/config.py`](../../core/config.py#L82-L97) — see DT-V1 below. |
| `vault/frontmatter.py` | `NoteMetadata` Pydantic model + `parse(path) -> Result[tuple[NoteMetadata, str]]` + `dumps(metadata, body) -> str`. Wraps `frontmatter.load` / `frontmatter.dumps`. Preserves unknown fields in `extra: dict`. |
| `vault/reader.py` | `read_note(path) -> Result[Note]` where `Note` is a frozen `@dataclass` with `content`, `metadata: NoteMetadata`, `path: Path`, `content_hash: str` (SHA-256 of body). |
| `vault/writer.py` | `write_note(path, content, metadata, source)` + `move_note(src, dst, source)`. Atomic temp-file-then-`os.replace`. Reads existing frontmatter on every write to check `updated_by_human`. Source is `Literal["ai", "human"]`. |
| `vault/indexer.py` | `scan_vault(root) -> list[VaultEntry]` + `detect_changes(entries, db_path) -> ChangeSummary`. Filesystem walk → reader → diff against `documents` table by `vault_path` + `content_hash`. |

**Existing code that constrains the design:**

- [core/config.py:68-97](../../core/config.py#L68-L97) — `VaultConfig` already exposes
  `root`, `inbox_path`, `projects_path`, `domain_path`, `documentation_path`,
  `synthesis_path`, `briefings_path`, `archive_path` as `@property` methods. This is the
  source of truth for vault folder names; `vault/paths.py` must read from it, not from a
  parallel constant table.
- [core/config.py:198-210](../../core/config.py#L198-L210) — `validate_vault_root_exists`
  already crashes at startup if `vault.root` is missing. `vault/paths.py` does **not** need
  to re-validate root existence; only subfolders.
- [core/result.py](../../core/result.py) — every public function in the vault layer returns
  `Success[T] | Failure`. Failures carry `recoverable: bool` and a `context: dict`. The
  writer's `updated_by_human` block returns `recoverable=False` because no retry will fix it.
- [core/exceptions.py:7](../../core/exceptions.py#L7) — `VaultError` is already declared;
  wrap IO errors and rethrow inside the module if internal helpers raise, but module
  boundaries always return `Result`, never raise.
- [storage/schema.sql:1-12](../../storage/schema.sql#L1-L12) — `documents` table holds
  `vault_path TEXT NOT NULL UNIQUE`, `content_hash TEXT`, `updated_by_human INTEGER NOT NULL
  DEFAULT 0`. The indexer reads these columns to compute `added` / `modified` / `deleted`.
- [STATE.md DECISION-001](../../STATE.md) — `documents.id` is integer; renames must be
  detected via `content_hash` match. The indexer needs to surface a `moved` set, not just
  `added/deleted` (see OQ-V3).
- [STATE.md DECISION-002](../../STATE.md) — `updated_by_human` is a **whole-note** boolean.
  Writer treats the entire file as off-limits when set; no per-field merging.
- [STATE.md Cross-Phase Constraint](../../STATE.md) — "Vault is source of truth for note
  content. `documents` table is an index only — it never stores note body or serves as a
  content cache." This forbids the writer from caching note bodies in SQLite. The mirror is
  metadata only (`vault_path`, `content_hash`, `updated_by_human`, `title`, `summary`,
  `note_type`, `confidence`, timestamps).
- [STATE.md TD-009](../../STATE.md) — `updated_by_human` sync between frontmatter and
  SQLite is an unresolved Phase 1 concern. Writer must update both, in that order, in the
  same transactional intent (see OQ-V2).
- `python-frontmatter` is already in [pyproject.toml:9](../../pyproject.toml#L9). Verified
  on disk at `.venv/lib/python3.12/site-packages/frontmatter/__init__.py`.

## How It Works

### `vault/paths.py`

Stateless functions, no class. Each function:

1. Reads the relevant root from `CONFIG.main.vault.<sub>_path` (lazy import — `CONFIG`
   validates at import time, so do the import inside the function body, matching the pattern
   in [storage/db.py:46-47](../../storage/db.py#L46-L47)).
2. Computes the final `Path` (appends date in ISO format, project name, etc.).
3. Calls `path.mkdir(parents=True, exist_ok=True)` on the **directory** the caller will
   write into.
4. Returns the `Path`.

Concrete helpers:

```python
def inbox() -> Path                          # CONFIG.main.vault.inbox_path
def archive() -> Path                        # CONFIG.main.vault.archive_path
def project_dir(name: str) -> Path           # projects_path / name
def domain_dir(name: str) -> Path            # domain_path / name
def documentation(project: str) -> Path      # documentation_path / f"{project}.md"
def briefings_today() -> Path                # briefings_path / f"{today_iso()}.md"
def briefings_for(date: date) -> Path        # briefings_path / f"{date.isoformat()}.md"
def synthesis_week(date: date) -> Path       # synthesis_path / f"{iso_week_of(date)}.md"
```

ISO date format only (e.g. `2026-04-25.md`), per the spec.

### `vault/frontmatter.py`

Two public functions, one Pydantic model:

```python
class NoteMetadata(BaseModel):
    type: str | None = None
    tags: list[str] = []
    project: str | None = None
    domain: str | None = None
    created: date | None = None
    updated: datetime | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    updated_by_human: bool = False
    summary: str | None = None
    source: str | None = None
    status: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
    model_config = {"extra": "allow"}  # see DT-V2

def parse(path: Path) -> Result[tuple[NoteMetadata, str]]:
    """Returns (metadata, body). Failure on YAML errors or missing file."""

def dumps(metadata: NoteMetadata, body: str) -> str:
    """Render metadata + body to a single string ready for atomic write."""
```

`parse` wraps `frontmatter.load(path)` — which returns a `frontmatter.Post` with
`.metadata: dict` and `.content: str`. Known fields populate `NoteMetadata`; unknown keys
land in `extra`. `dumps` re-merges `extra` into `metadata` dict, builds a
`frontmatter.Post(content=body, **merged)`, then `frontmatter.dumps(post)`.

Verified locally: `frontmatter.dumps` calls `yaml.safe_dump`, which:
- Sorts keys alphabetically. Fine — Obsidian doesn't care about key order.
- Serializes `list[str]` as block-list (`- item`), which Obsidian requires. The reference's
  `formatYamlTags` helper is **not needed in Python**.
- Quotes strings containing YAML-special characters automatically.

### `vault/reader.py`

```python
@dataclass(frozen=True)
class Note:
    path: Path
    metadata: NoteMetadata
    content: str           # body without frontmatter
    content_hash: str      # sha256(body.encode("utf-8")).hexdigest()

def read_note(path: Path) -> Result[Note]:
    ...
```

Hash is computed over the **body only**, not the raw file. Justification:
- A pure-AI metadata update (e.g. raising `confidence` from 0.7 to 0.9) should not
  invalidate the search index or cause the embedder to re-embed identical content.
- This matches `documents.content_hash` semantics used by the storage layer (see
  [docs/research/storage_level.md](storage_level.md)).

Failure modes returned, not raised:
- `FileNotFoundError` → `Failure(recoverable=False, error="note not found: ...")`
- `UnicodeDecodeError` → `Failure(recoverable=False, ...)` (corrupt or binary file)
- `yaml.YAMLError` from frontmatter parse → `Failure(recoverable=False, error="malformed
  frontmatter at ...")`
- `PermissionError` → `Failure(recoverable=True, ...)`

### `vault/writer.py` — load-bearing

The trust model. Every write is a four-step transaction:

```
1. If path exists: read current frontmatter via vault/frontmatter.parse.
   - If updated_by_human=True AND source="ai": return Failure(recoverable=False).
2. Merge incoming metadata with kept fields (created stays the same; updated bumps to now;
   updated_by_human is set False for "ai" and True for "human").
3. Atomic write:
       tmp = NamedTemporaryFile(dir=path.parent, delete=False, mode="w", encoding="utf-8")
       tmp.write(rendered)
       tmp.flush(); os.fsync(tmp.fileno()); tmp.close()
       os.replace(tmp.name, path)
   `os.replace` is atomic across same-filesystem renames on POSIX and Windows. Same-dir
   tempfile guarantees same-filesystem.
4. On any exception during 1–3: os.unlink(tmp.name) and return Failure. Never leave a
   partial file on disk.
```

`move_note(src, dst, source)` is a separate atomic operation:
1. Read src frontmatter → same `updated_by_human` check.
2. `dst.parent.mkdir(parents=True, exist_ok=True)`
3. `os.replace(src, dst)` — atomic on same FS.
4. If different filesystems (vault straddles a mount): fall back to copy+fsync+unlink with
   an in-flight `.tmp` suffix. Document this fallback rather than silently lose atomicity.

**Source is always passed explicitly** — never inferred. CLI commands pass `"human"` when
the user triggered the action directly, `"ai"` when a pipeline did. This is intentional
friction (see the feature spec: "this is a deliberate friction point").

**`Literal["ai", "human"]`** as the type — Pydantic / mypy will catch typos at call sites.

**SQLite mirror** (OQ-V2): The writer should ALSO upsert into `documents` (or trigger the
indexer). The cross-phase constraint requires `updated_by_human=1` to be visible in SQLite
so future code can answer "is this note off-limits?" without filesystem I/O. Either the
writer does this directly (tight coupling vault→storage) or it returns a `WriteOutcome` and
the caller is required to call the indexer next. Plan must resolve.

### `vault/indexer.py`

```python
@dataclass(frozen=True)
class VaultEntry:
    path: Path                  # absolute path on disk
    vault_path: str             # relative to vault.root, POSIX separator
    content_hash: str
    metadata: NoteMetadata

@dataclass(frozen=True)
class ChangeSummary:
    added:    list[VaultEntry]
    modified: list[VaultEntry]
    deleted:  list[str]         # vault_path strings, no live Path objects for deleted files
    moved:    list[tuple[str, VaultEntry]]  # (old_vault_path, new_entry)

def scan_vault(root: Path) -> list[VaultEntry]: ...
def detect_changes(current: list[VaultEntry], db_path: Path | None = None) -> ChangeSummary: ...
```

`scan_vault` walks `root` using `os.walk` or recursive `Path.iterdir`. Filters:

| Filter | Reason |
|---|---|
| `.git/`, `.obsidian/`, `.trash/`, `.stversions/`, `node_modules/` | Tool/system folders. Reference uses the same list. |
| Any name starting with `.` if also in IGNORE_DIRS | Don't blanket-block dot-prefixed; Obsidian-style `.system/` is legitimate. |
| `.DS_Store`, `Thumbs.db` | OS noise. |
| `*.sync-conflict-*.md` | Obsidian Sync conflict files. Reference handles this; our boss likely uses Obsidian Sync. |
| Non-`.md` files | Out of scope for vault notes. Attachments live alongside but are not indexed. |
| Symlinks | Skip on first scan — the reference doesn't address this. Recursive symlink loops crash the walker. Use `Path.is_symlink()` before recursing. |

`detect_changes` opens a read-only connection (`get_connection(readonly=True)` —
[exists in storage/db.py:65](../../storage/db.py#L65)), pulls `vault_path`, `content_hash`
from `documents`, and computes set diffs:

- `added`: in `current`, not in DB.
- `modified`: in both, hashes differ.
- `deleted`: in DB, not in `current` (after the moved set is removed).
- `moved`: deleted + added that share a `content_hash`. DECISION-001 mandates this check.

INFO log on completion: `"Scan complete: X added, Y modified, Z deleted, W moved (total
N)"`.

## Edge Cases & Silent Failure Modes

1. **PyYAML 1.1 boolean quirk** — bare `yes` / `no` / `on` / `off` / `y` / `n` parse to
   booleans. Verified: `yaml.safe_load("custom: yes")` returns `{"custom": True}`. If a
   note's frontmatter has `status: on`, `NoteMetadata.status` (declared as `str | None`)
   will fail validation because Pydantic gets a `bool`. Mitigation: pre-coerce known string
   fields in the wrapper before passing to Pydantic, OR add a `field_validator(mode="before")`
   that stringifies booleans. **Five minutes of awareness, per the spec — the plan must pick
   one.**

2. **Notes without frontmatter at all.** Many human-authored notes have no `---` block.
   `frontmatter.load` handles this gracefully (`metadata={}`, `.content` is the full file).
   `NoteMetadata()` with all defaults is the parse result. **Do not** treat this as an
   error.

3. **Malformed frontmatter** — unterminated `---`, tab-indented YAML, etc. Common in user
   vaults. `parse` must return `Failure(recoverable=False)` with the path and YAML error
   line if available. Pipelines need to know which file blew up; surface in `context`.

4. **`datetime` vs `date` in frontmatter.** Obsidian writes `created: 2026-04-25` (bare
   ISO date — PyYAML returns `datetime.date`). Some plugins write
   `created: 2026-04-25T08:00:00Z` (returns `datetime.datetime`). `NoteMetadata` must
   accept both; consider `created: date | datetime | None`.

5. **Atomic write — fsync of the directory.** On Linux, `os.replace` is atomic with respect
   to readers but the rename itself is journaled separately. For full crash safety the
   parent directory should be fsynced after replace. This is overkill for our threat model
   (developer laptop, not a database). Note it; do not do it.

6. **Race: AI writes while user is mid-edit.** Reader-writer race between user's Obsidian
   editor and our writer is unavoidable without OS-level locks. The `updated_by_human`
   guard is the intended mitigation — if the user touched the note, they should set the
   flag (or AI sets it on detected human edit). MCP daemon scenario (Phase 4) makes this
   sharper because tools run concurrently with editing. Flag for Phase 4.

7. **Concurrent indexer + writer.** Reference uses a module-level `indexing = true` lock.
   In our async pipeline runner (DECISION-010), two concurrent capture runs could race the
   writer. Solution deferred to Phase 4 — single-shot CLI is safe.

8. **Indexer + sync-conflict files.** When Obsidian Sync creates `note.sync-conflict-...
   md`, both files exist briefly. Indexer must skip the conflict file (reference does);
   never auto-resolve.

9. **Reader called on the writer's temp file.** If the indexer scans while a writer is
   mid-write, the temp file (e.g. `tmpXXXX.md`) could appear in the listing. Mitigation:
   name the temp file with a `.` prefix (`.tmp_<uuid>.md`) — indexer's dot-prefix filter
   skips it.

10. **Empty vault folders.** `briefings_path / "2026-04-25.md"` — the directory exists
    because `mkdir(exist_ok=True)`, but no file. Reader returns `Failure(FileNotFoundError)`
    cleanly. Documented behavior, not an edge case to fix.

11. **Path normalization on macOS HFS+/APFS.** Macos can return composed vs decomposed
    Unicode for filenames containing accented characters (NFC vs NFD). `vault_path` strings
    stored in SQLite may not match strings produced by a fresh scan. If the boss uses
    accented characters in note names, this can cause spurious `added` + `deleted` pairs.
    Mitigation: `unicodedata.normalize("NFC", str(path))` before storing/comparing. Cite
    it; plan must decide whether to implement Phase 0 or defer.

12. **Body trailing newline drift.** Some tools (and `frontmatter.dumps`) emit a trailing
    newline; others don't. A round-trip can flip a hash and report a phantom `modified`.
    Mitigation: strip trailing whitespace from `body` before hashing AND before writing,
    consistently. Spec for reader.py implies the hash is the source of truth, so this
    matters.

## Dependencies & Coupling

```
                   ┌────────────────────────┐
                   │  core/config.py        │  CONFIG.main.vault.<sub>_path
                   └───────────┬────────────┘
                               │
       ┌───────────────────────┼─────────────────────┐
       │                       │                     │
┌──────▼──────┐         ┌──────▼─────────┐    ┌──────▼─────────┐
│ paths.py    │         │ frontmatter.py │    │ reader.py      │
│ (Path only) │         │ (Pydantic +    │    │ uses           │
│ no I/O      │         │  python-       │    │ frontmatter.py │
│ except      │         │  frontmatter)  │    │ + hashlib      │
│ mkdir       │         └──────┬─────────┘    └────────┬───────┘
└─────────────┘                │                       │
                               │ ┌─────────────────────┘
                               ▼ ▼
                         ┌──────────────┐         ┌────────────────────┐
                         │ writer.py    │◀────────│ indexer.py         │
                         │ (atomic +    │         │ scan + diff vs DB  │
                         │ updated_by_  │         │                    │
                         │ human gate)  │         └────────┬───────────┘
                         └──────┬───────┘                  │
                                │                          │
                                ▼                          ▼
                         ┌────────────────────────────────────────┐
                         │ storage/db.py + documents (mirror)     │
                         └────────────────────────────────────────┘
```

**Outbound** — what the vault layer uses:
- `pathlib`, `hashlib`, `os`, `tempfile` (stdlib)
- `python-frontmatter`, `pyyaml` (declared deps)
- `pydantic` (for `NoteMetadata`)
- `core.config`, `core.result`, `core.exceptions`
- `storage.db.get_connection` (indexer only; reader/writer/paths/frontmatter never touch SQL
  directly per the storage research split)

**Inbound** — what will use the vault layer:
- `pipelines/capture.py` (Phase 1) — writer, frontmatter, paths.
- `pipelines/classify.py` (Phase 2) — writer.move_note for inbox→Projects, paths.
- `retrieval/keyword.py` (Phase 3) — indexer to keep FTS5 in sync.
- `briefings/daily.py` (Phase 8) — paths.briefings_today, writer.
- `mcp_server/tools.py` (Phase 4) — only through pipelines, never direct.

## Reference Project Patterns

The JS reference (`docs/reference/knowledge-base-server/src/`) implements an analogous
subsystem. Inventory:

| Reference file | What it does | Adopt? |
|---|---|---|
| `src/paths.js` | Static paths (`KB_DIR`, `FILES_DIR`, `DB_PATH`) under `~/.knowledge-base`. Also `mkdirSync(FILES_DIR, recursive: true)` at import time. | **Skip.** Our [VaultConfig](../../core/config.py#L68-L97) already plays this role and is config-driven, not home-dir-hardcoded. The reference's home-dir convention is for a multi-user server; ours is a single-vault CLI. |
| `src/utils/frontmatter.js` (`formatYamlTags`) | Hand-rolled YAML block-list serializer for Obsidian compatibility. | **Skip.** Reason: gray-matter (JS) emits flow-sequence `[a, b]` which Obsidian dislikes. PyYAML's `safe_dump` already emits block-list by default. Verified locally. |
| `src/vault/parser.js` (`parseVaultNote`) | Infers note `type` from folder path (`05_research/...` → `research`). Inserts `frontmatter`-derived fields into a flat object. | **Adapt selectively.** Drop the `FOLDER_TYPE_MAP` — our taxonomy comes from the classifier, not the folder. Keep the "fall back to filename for title" behavior in any reader that needs a display title (probably not Phase 0 — defer to Phase 1 Capture). |
| `src/vault/indexer.js` (`scanVault`, `indexVault`) | Walks vault, hashes content, upserts into a `vault_files` tracking table, optionally embeds. Module-level `indexing` lock. | **Adopt walk + ignore lists.** `IGNORE_DIRS = {'.obsidian', '.trash', '.git', ...}`, `IGNORE_FILES = {'.DS_Store', 'Thumbs.db'}`, `.sync-conflict-*` skip. **Skip the module-level lock** — async pipelines need scoped concurrency control, not a global flag (DECISION-010 implies). **Skip the embeddings hook** — Phase 3 owns embeddings; vault layer must not call sentence-transformers. **Adapt the hash** — reference uses sha256 sliced to 16 chars; we use full sha256 to match `documents.content_hash` schema. |
| Reference tests (`tests/vault-*.test.js`) | Use `mkdtempSync` + temp-dir vaults, write fixture markdown, assert scan output. | **Adopt.** Same pattern with `pytest`'s `tmp_path` fixture. Our `tests/test_vault/` directory exists but is empty — populate with at least: malformed frontmatter, no-frontmatter, sync-conflict, hidden folder, valid note. |

**Pattern not in the reference but we need:** atomic write via temp-file-then-`os.replace`.
The JS reference uses `writeFileSync` directly, which is **not atomic** — a crash mid-write
corrupts the note. Our writer must be stricter than the reference here because the
`updated_by_human` model assumes notes are never half-written.

## Open Questions

| ID | Question | Why I can't answer from the code |
|---|---|---|
| **OQ-V1** | Should `paths.py` duplicate `VaultConfig.inbox_path` etc. as `inbox()`, or only expose the **parametrized** helpers (`briefings_for(date)`, `project_dir(name)`)? Duplication eases discoverability (one import: `from vault.paths import *`) but creates two "right" ways to spell the same thing. | Design decision. Spec lists both kinds of helpers. Plan must pick a single canonical form. Recommended: paths.py exposes ONLY parametrized helpers; static folder roots stay on `VaultConfig`. |
| **OQ-V2** | Does `vault/writer.py` write to the `documents` SQLite mirror directly, or does it only touch the filesystem and rely on the indexer to sync? | The cross-phase constraint says `updated_by_human=1` must be query-able from SQLite. Two designs satisfy this: (a) writer calls `storage/documents.upsert(...)` after the atomic FS write; (b) writer is FS-only and every CLI command runs `indexer.detect_changes` afterwards. (a) is faster but couples vault→storage; (b) is cleaner but slower and risks drift if the indexer is skipped. STATE.md TD-009 calls this out but doesn't resolve. |
| **OQ-V3** | Does the indexer return a `moved` set in Phase 0, or only `added`/`modified`/`deleted` as the spec literally requires? | DECISION-001 says vault indexer "MUST run `SELECT id FROM documents WHERE content_hash = ? AND vault_path != ?` before inserting to detect moves." The spec's `ChangeSummary` doesn't list `moved`. Conflict: plan must decide. Recommended: add `moved`, because without it Phase 1 has to re-implement move detection inside the capture pipeline. |
| **OQ-V4** | How should the `NoteMetadata` model handle PyYAML's bool quirk for known string fields (`status: on` → `True`)? | Two valid mitigations: a `field_validator(mode="before")` per string field, or a pre-parse normalizer in `vault/frontmatter.parse`. Plan picks one. The cost of getting this wrong is silent: a note's `status` becomes literal `True`, and downstream pipelines compare strings. |
| **OQ-V5** | Body hashing: do we strip trailing newline before hashing? `frontmatter.dumps` adds one; round-trip without strip causes phantom `modified`. | Spec doesn't address. Recommendation: yes, strip with `body.rstrip("\n")` consistently in reader, writer, and the hasher. Document the rule. |
| **OQ-V6** | Unicode normalization (NFC vs NFD) for `vault_path` strings on macOS. | Cannot determine without knowing whether the user's vault contains accented filenames. Cheap to add (`unicodedata.normalize("NFC", ...)`); safer to add now than to debug ghost-duplicates later. |
| **OQ-V7** | Should the writer fsync the parent directory after `os.replace`? | Not derivable from code — depends on durability requirements. Default no (developer-grade); add if a future incident shows we need it. |

These are unanswerable from the code alone — each is a human judgment about coupling,
durability, or scope. They are not a dump for things I didn't trace.

## Technical Debt Spotted

Items worth flagging now even though they are outside Phase 0 scope:

| ID | What | Defer to |
|---|---|---|
| TD-V1 | The writer's `documents` mirror sync (OQ-V2 resolution) — keep an explicit hook in writer.py for Phase 1 to fill in even if Phase 0 leaves it as a TODO `pass`. | Phase 1 |
| TD-V2 | Per-section authorship (already TD-006 in STATE.md). When MCP tools need "AI wrote summary, human wrote conclusion," `updated_by_human` is too coarse. | Phase 7+ |
| TD-V3 | Concurrency-safe writer for Phase 4 MCP daemon. `os.replace` is atomic but the read-then-write `updated_by_human` check is not. Need a file lock or DB-side optimistic concurrency. | Phase 4 |
| TD-V4 | Move detection across edited content (Q-001 from STATE.md). `content_hash` based move detection breaks if a note is edited AND renamed simultaneously. Frontmatter `doc_id` is the long-term fix. | Phase 1 |
| TD-V5 | Unicode normalization for macOS-authored vaults (OQ-V6 if not adopted). | Phase 1 |
| TD-V6 | Symlink handling — first cut skips them. If the user starts symlinking attachments into the vault, indexer needs to follow them once without looping. | Phase 1+ |

## Downstream phase impact

- **Phase 1 (Capture)** — first real consumer. `pipelines/capture.py` calls `paths.inbox()`,
  `reader.read_note` to extract, `writer.write_note(..., source="ai")` to persist the
  AI-augmented frontmatter back into the same file. Without a working writer, Capture is a
  no-op.
- **Phase 2 (Classify)** — calls `writer.move_note(src=inbox/foo.md,
  dst=projects/X/foo.md, source="ai")`. Without atomic move, a crash mid-classify can lose
  the note entirely.
- **Phase 3 (Search)** — calls `indexer.scan_vault` + `detect_changes` at startup and after
  every Capture. FTS5 sync triggers off the `documents` table that the indexer maintains.
- **Phase 4 (MCP)** — long-running daemon. Surfaces OQ writer concurrency (TD-V3).
- **Phase 6 (Documentation)** — relies hard on `updated_by_human`. AI proposes; human
  confirms; flag is the ratchet. If the writer's check is wrong, all Documentation is
  unsafe.
- **Phase 8 (Briefing)** — calls `paths.briefings_today()` + `writer.write_note(...,
  source="ai")`. Briefings overwrite the same daily file when re-run; idempotency matters.

## Self-review notes

- Verified VaultConfig already exposes path properties — flagged the duplication with
  paths.py as OQ-V1 rather than glossing over it.
- Verified `python-frontmatter` is installed and `frontmatter.dumps` emits block-list tags
  by default (ran `python3 -c "..."` to confirm) — explicitly declined to copy the
  reference's `formatYamlTags` helper with that evidence.
- Verified PyYAML 1.1 boolean quirk experimentally (`yes` → `True`) — turned the
  spec's "five minutes of awareness" into a concrete OQ-V4.
- Did not trace `core/audit.py` deeply because the vault layer does not call audit
  directly — audit writes come from pipelines, not from the vault module. Surface noted; no
  open question.
- Cross-checked every recommendation against STATE.md decisions and cross-phase
  constraints; no contradictions, two unresolved tensions surfaced as OQ-V2 and OQ-V3.
- Reference patterns: every "adopt" entry has a one-line reason; every "skip" cites the
  reason the reference's motivation doesn't apply here.
