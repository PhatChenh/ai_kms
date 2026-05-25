# Research: revise_attachment_layout
_Last updated: 2026-05-23_

> **Supersedes** the OLD-layout sections of [docs/research/capture_pipeline.md#Non-md branch (PDF, DOCX)](capture_pipeline.md) (sibling next to source; single global `attachment/`) and the matching language in `docs/roadmap.md` Phase 1 (l. 53–66). Those documents still describe the **shipped** code. This file documents the **target** layout for Brief #1 of the rework.
>
> **Scope**: vault-layer primitives only. Capture pipeline branching (Brief #2: `attachment_capture_pipeline`) and sync/archive (Brief #3: `attachment_sync_and_archive`) are out of scope.

## Overview

Today the vault has a single global `Vault/attachment/` folder; every non-md binary is moved there and a sibling `.md` is written next to the source in `inbox/` (or wherever the drop landed). The revise puts the binary into a per-Project/Domain `attachment/` subfolder and the sibling into a hidden `.summaries/` subfolder of that same attachment folder. The vault-layer primitives that must adapt are: `vault/indexer.py` (dotfolder pruning blocks `.summaries/`), `vault/paths.py` (no per-Project/Domain attachment/summaries helpers), `core/config.py::VaultConfig` (single global `attachment_path` no longer represents the layout), and the sibling row in `documents` (which `vault_path` does the row carry — the sibling, the binary, or both?). `vault/writer.py::move_attachment` does **not** need a signature change — the caller already computes `dst`. `vault/reader.py` and the storage schema are untouched. `vault/frontmatter.py` can carry an optional `attachment_path` pointer via its existing `extra` dict without a `_KNOWN_KEYS` change.

## Key Components

| File | What it does today | Touch needed |
|---|---|---|
| [vault/writer.py](../../vault/writer.py) | `write_note`, `move_note`, `move_attachment(src, dst)`, `_atomic_write` | None for signatures; behavior unchanged. Callers compute new dst. |
| [vault/indexer.py](../../vault/indexer.py) | `scan_vault`, `scan_non_md_drops`, `detect_changes`, `IGNORE_DIRS`, `IGNORE_FILES` | Dotfolder prune must allowlist `.summaries/`. `scan_non_md_drops` attachment-subtree skip must generalize. |
| [vault/paths.py](../../vault/paths.py) | `project_dir`, `project_materials`, `project_index`, `domain_dir`, `domain_notes`, `domain_index`, `to_vault_path`, `load_valid_domains` | Add `project_attachment(name)`, `project_summaries(name)`, `domain_attachment(name)`, `domain_summaries(name)`. |
| [core/config.py](../../core/config.py) `VaultConfig` (l. 68-100) | `attachment_dir` field + `attachment_path` property + 7 other `_path` properties | Per-Project/Domain layout dissolves the global `attachment_path`. Add `summaries_subdir`. Decide: keep global property as legacy/staging or remove (see OQ-AL2). |
| [vault/frontmatter.py](../../vault/frontmatter.py) `NoteMetadata`, `_KNOWN_KEYS`, `parse`, `dumps` | Pydantic `extra="ignore"`; unknown YAML keys collected into `NoteMetadata.extra` dict by `parse()`; `dumps()` writes them back | Adding `attachment_path` as a known field requires updating `_KNOWN_KEYS` + adding a Field. Cheaper alternative: leave it in `extra` (no schema churn) — round-trips cleanly. |
| [storage/schema.sql](../../storage/schema.sql) + [storage/documents.py](../../storage/documents.py) | `documents.vault_path TEXT NOT NULL UNIQUE`; `upsert(WriteOutcome)`, `rename(old, new)`, `all_paths()`, `delete_by_path()`, `replace_path()` | None. Schema unchanged. `vault_path` accepts any vault-relative POSIX string. Decision of which path the row carries (sibling vs attachment vs both) is OQ-AL1. |
| [vault/reader.py](../../vault/reader.py) | `read_note(path)` — purely path-agnostic via frontmatter.parse | None. Reads any `.md` regardless of location. |
| [pipelines/capture.py](../../pipelines/capture.py) l. 437-495 (`_store_nonmd`), l. 623-631 (scan loop) | Builds `attachment_dst = attachment_dir / f"{stem}{suffix}"`; sibling at `src.parent / f"{stem}.md"`; calls `move_attachment`; calls `documents.upsert(sibling_outcome)`; uses `scan_non_md_drops(root, attachment_path)` | Out of scope (Brief #2). Listed here only because it is the sole non-test caller of `attachment_path` and `move_attachment`. |
| [vault/watcher.py](../../vault/watcher.py) (Phase 11 — shipped) | `attachment_path` parameter used to skip events under that subtree | Out of scope (Brief #3 sync mechanics). Listed because per-project layout breaks the single-subtree skip assumption — flag for Brief #3. |

## How It Works (target behavior, with implementation specifics)

### Layout, target

```
Vault/
├── inbox/                    ← single drop zone (unchanged)
├── Projects/
│   └── <A>/
│       ├── CLAUDE.md         ← out of scope (TD-015)
│       ├── <user notes>.md
│       └── attachment/
│           ├── report.pdf    ← binary, indexer skips
│           └── .summaries/   ← hidden; indexer must traverse
│               └── report.md ← sibling, indexed into documents
├── Domain/
│   └── <D>/
│       ├── CLAUDE.md
│       ├── context.yaml
│       ├── notes/
│       └── attachment/       ← analogous to Project
│           ├── *.pdf
│           └── .summaries/
└── …
```

The global `Vault/attachment/` folder is removed.

### `vault/indexer.py` — the one allowlist break

`scan_vault` (l. 119-190) and `scan_non_md_drops` (l. 74-116) both prune directories with:

```python
dirnames[:] = [
    d for d in dirnames
    if d not in IGNORE_DIRS
    and not d.startswith(".")
    and not (dirpath / d).is_symlink()
]
```

The `d.startswith(".")` clause is the one and only blocker for `.summaries/`. The minimum-touch change is:

```python
if d not in IGNORE_DIRS
and not (d.startswith(".") and d != ".summaries")
and not (dirpath / d).is_symlink()
```

or, more readable:

```python
_DOT_ALLOWLIST = frozenset({".summaries"})
…
and (not d.startswith(".") or d in _DOT_ALLOWLIST)
```

`Path.walk` semantics: mutating `dirnames` **in place** with slice assignment controls recursion — verified by reading the loop (`dirnames[:] = [...]`). A subsequent `dirnames` rebuild on the next iteration produces the children of the next dirpath, not a revisit. The allowlist works correctly.

`scan_vault`'s file filter (l. 153) `if name.startswith("."): continue` will reject `.summaries/<x>.md` if the file name itself starts with a dot, but `.summaries/report.md` is `report.md` (filename does not start with `.`). The dot is only on the parent directory. So filename filter is fine.

`scan_non_md_drops` has the same dotfolder prune at l. 91-97 + a dotfile filter at l. 100. The attachment-subtree skip at l. 111 (`if attachment_path in file_path.parents`) is single-subtree; in the new layout there is no single attachment_path. This skip must generalize to "any folder named `attachment/` whose parent is a `Projects/<*>` or `Domain/<*>` folder" — flagged forward to Brief #2/#3, since `scan_non_md_drops` is the entry point for the capture pipeline's "find loose binaries to ingest" loop (it must NOT enumerate binaries already inside an `attachment/`).

`detect_changes` (l. 193-262) is path-agnostic. It operates on `vault_path` strings from `scan_vault` and `documents.all_paths()`. Whatever the indexer chooses to store as `vault_path` (sibling or attachment — see OQ-AL1) flows through unchanged.

`IGNORE_FILES = frozenset({".DS_Store", "Thumbs.db", "CLAUDE.md"})` — CLAUDE.md is intentionally excluded from indexing (out of scope here, but relevant: per-folder CLAUDE.md inside `Projects/<A>/` and `Domain/<D>/` is still ignored by the global filename rule, which is correct).

### `vault/writer.py::move_attachment` — signature stays put

Current signature: `move_attachment(src: Path, dst: Path) -> Result[Path]`. The caller computes `dst`. In the new layout the caller computes a different `dst` (per-project), but `move_attachment` itself doesn't care. Confirmed:

- l. 265: `dst.parent.mkdir(parents=True, exist_ok=True)` — auto-creates `Projects/<A>/attachment/` if absent.
- l. 267-272: refuses to overwrite — caller picks a non-colliding name; per-folder naming collisions are *new* (today's global `attachment/` had global collisions; per-folder layout means same-named PDF in two projects no longer collides). The collision loop in `pipelines/capture.py:459-468` (caller-side) handles this.
- l. 276-289: cross-filesystem fallback (EXDEV) writes `.tmp_<uuid><suffix>` next to `dst` (i.e. inside `Projects/<A>/attachment/`). The dot-prefix tmp file is invisible to `scan_non_md_drops` (skipped by `name.startswith(".")` at l. 100) and `scan_vault` (skipped at l. 153). Safe.

`_atomic_write` (l. 87-103) writes `.tmp_<uuid>.md` inside `dst.parent`. When the caller writes a sibling at `Projects/<A>/attachment/.summaries/report.md`, the tmp file lives in `.summaries/` — also dot-prefixed, doubly invisible. Safe.

**Critical pipeline-layer note (forward to Brief #2)**: today the sibling is written at `src.parent / f"{stem}.md"` ([pipelines/capture.py:478](../../pipelines/capture.py)). That is **next to the source** (typically inbox/). The new design needs the sibling written at `<target_attachment>/.summaries/<stem>.md` — a strictly different location. This is a caller-side change; `write_note(path, body, meta, actor)` itself is path-agnostic and `_atomic_write` works regardless of depth.

### `vault/paths.py` — new helpers needed

Today's helpers (l. 57-104): `project_dir(name)`, `project_materials(name)`, `project_index(name)`, `domain_dir(name)`, `domain_notes(name)`, `domain_index(name)`, plus orthogonal `documentation`, `briefings_*`, `synthesis_week`.

All follow the same shape: lazy-import CONFIG, build `CONFIG.main.vault.<root>_path / name / <subdir>`, `mkdir(parents=True, exist_ok=True)`, return the Path. They do not write files.

The new layout requires four new helpers in the same shape:

```python
def project_attachment(name: str) -> Path:
    """Return Projects/<name>/attachment/ and ensure it exists."""

def project_summaries(name: str) -> Path:
    """Return Projects/<name>/attachment/.summaries/ and ensure it exists."""

def domain_attachment(name: str) -> Path:
    """Return Domain/<name>/attachment/ and ensure it exists."""

def domain_summaries(name: str) -> Path:
    """Return Domain/<name>/attachment/.summaries/ and ensure it exists."""
```

The subdir names (`"attachment"`, `".summaries"`) come from VaultConfig (see next section).

`to_vault_path(absolute)` (l. 41-54) — NFC-normalises with `unicodedata.normalize("NFC", rel)`. Already works on any path under vault root. `.summaries/` is ASCII; project/domain names may be Vietnamese (NFC composes diacritics). The normalization makes the sibling vault_path stable across macOS NFD↔NFC drift (DECISION-017). No change.

`load_valid_domains(vault_root)` (l. 22-38) — reads `Vault/Domain/<x>/` folder names, excludes dotfiles. Would correctly pick up `Domain/Uncategorized/` if/when it exists (Brief #3 concern).

### `core/config.py::VaultConfig` — global `attachment_path` decision

Current (l. 68-100):

```python
class VaultConfig(BaseModel):
    root:              Path
    inbox_dir:         str = "inbox"
    projects_dir:      str = "Projects"
    domain_dir:        str = "Domain"
    documentation_dir: str = "Documentation"
    synthesis_dir:     str = "Synthesis"
    briefings_dir:     str = "Briefings"
    archive_dir:       str = "Archive"
    attachment_dir:    str = "attachment"

    @property
    def inbox_path(self)         -> Path: return self.root / self.inbox_dir
    @property
    def projects_path(self)      -> Path: return self.root / self.projects_dir
    @property
    def domain_path(self)        -> Path: return self.root / self.domain_dir
    @property
    def documentation_path(self) -> Path: return self.root / self.documentation_dir
    @property
    def synthesis_path(self)     -> Path: return self.root / self.synthesis_dir
    @property
    def briefings_path(self)     -> Path: return self.root / self.briefings_dir
    @property
    def archive_path(self)       -> Path: return self.root / self.archive_dir
    @property
    def attachment_path(self)    -> Path: return self.root / self.attachment_dir
```

After the rework:

- `attachment_dir = "attachment"` — **keep**; reused by `project_attachment(name)` and `domain_attachment(name)` helpers to know the subdir name.
- `attachment_path` property — `Vault/attachment/` no longer exists. Either:
  - **(Option-VC-A)** Remove the property. Every caller of `CONFIG.main.vault.attachment_path` becomes a TypeError, forcing migration. Clean.
  - **(Option-VC-B)** Repurpose as **staging area** for low-confidence drops where AI cannot decide a project (Brief #2 OQ-AC3). Document the new role; keep the property.
  - **(Option-VC-C)** Keep property but point at a deprecated/empty path; remove in a follow-up.
- New field: `summaries_subdir: str = ".summaries"` — used by the new path helpers.

This is **OQ-AL2** below — decision deferred to plan stage; this research only surfaces the trade-offs.

`archive_path` is touched by Brief #3 (per-Domain Archive); leave untouched here.

### `documents.vault_path` — what the row points to (OQ-AL1, the big one)

Schema:

```sql
CREATE TABLE IF NOT EXISTS documents (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    vault_path       TEXT NOT NULL UNIQUE,
    title            TEXT NOT NULL,
    summary          TEXT,
    note_type        TEXT,
    confidence       REAL,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_by_human INTEGER NOT NULL DEFAULT 0,
    content_hash     TEXT
);
```

DECISION-001: synthetic integer PK; `vault_path` is mutable UNIQUE. DECISION-018: indexer indexes only `.md`. Therefore the row produced by markdown indexing of `Projects/<A>/attachment/.summaries/report.md` will exist; the question is what string lands in `vault_path`.

Three concrete options surface from the discussion file. Each is analyzed below — **no recommendation; defer to plan**.

#### Option A: `vault_path = sibling-only`

`vault_path = "Projects/<A>/attachment/.summaries/report.md"` (NFC-normalized). The binary is invisible to `documents`. The sibling's body contains an Obsidian link to `../report.pdf`.

- **Indexer interaction**: `scan_vault` already produces this exact path (it walks `.summaries/` and emits a `VaultEntry` for `report.md`). `detect_changes` works unchanged. `documents.upsert(WriteOutcome)` takes `outcome.vault_path` which equals the sibling path. Zero schema change.
- **Search hit semantics**: a user/AI clicks a search result → opens the sibling `.md` (which contains the summary text and a wikilink to the binary). One extra click to view the source.
- **Embedding source (Phase 3)**: embedding is computed from the sibling's body — which already contains the AI-generated summary of the binary. Coherent.
- **FK stability under rename**: if `report.pdf` is renamed to `q1.pdf`, the sibling stays at `report.md` (until sync fires — Brief #3). Search hit still opens the sibling whose link is now broken. The `documents` row's `vault_path` is stable.
- **Attachment-only access**: AI tools asking "show me the PDFs in project A" must do a separate filesystem scan; `documents` cannot answer.
- **MCP `kms_search` (Phase 4)**: returns sibling path. Boss clicking it in Obsidian opens the summary first. Whether the boss wants the summary or the source first is a UX decision that depends on her workflow — flag for plan.

#### Option B: `vault_path = attachment-only`

`vault_path = "Projects/<A>/attachment/report.pdf"`. The sibling `.summaries/report.md` is NOT in the `documents` table; it exists only as the artifact that gets parsed to produce the row.

- **Indexer interaction**: BREAKS the existing flow. `scan_vault` emits a `VaultEntry` for the `.md` it walked (sibling path), not the binary. To produce a row keyed by the binary's path, the indexer would need a special case: "if file is inside `.summaries/`, rewrite `vault_path` to the parent attachment, with extension swap." That's an explicit DECISION-018 weakening: indexer still reads only `.md`, but it now stores a path that *isn't* the file it read. Confusing; surface in plan as a real cost.
- **`content_hash`**: would hash the sibling body (since that is what was read), but `vault_path` points at the binary. Move detection (`detect_changes` Q-001) collapses deleted+added pairs by content_hash — collisions are now between *summaries*, but identified by *binary* paths. Implementable, but the abstraction leaks.
- **Schema integrity (DECISION-018 conflict)**: the `documents` table semantics today is "one row per indexed markdown note." A row whose `vault_path` is `.pdf` violates that semantic without a schema change to add an `attachment_path` column or similar. Tracking attachments directly is the path DECISION-018 explicitly closed off ("If a future phase needs to track attachments, that would require a separate `attachments` table and a different indexer" — STATE.md l. 173).
- **Search hit semantics**: clicking a hit opens the PDF directly. No sibling browse. Good UX for "I just want the source."
- **Verdict (factual only, no rec)**: this option is the most expensive — it would either require changing DECISION-018 or introducing a special-case path rewrite in the indexer. **Worth flagging as the highest-cost option.**

#### Option C: hybrid (`vault_path = sibling` + frontmatter `attachment_path`)

`vault_path = "Projects/<A>/attachment/.summaries/report.md"` (same as Option A). The sibling's frontmatter carries an explicit pointer:

```yaml
---
type: attachment-summary
attachment_path: ../report.pdf
…
---
```

Or absolute-style: `attachment_path: Projects/<A>/attachment/report.pdf`.

- **Indexer interaction**: identical to Option A. The frontmatter field rides along on the sibling, parsed by `vault/frontmatter.py`.
- **Frontmatter mechanics**: `_KNOWN_KEYS` does not currently include `attachment_path`. Two routes:
  1. Add `attachment_path: str | None = None` to `NoteMetadata` + add the key to `_KNOWN_KEYS`. Typed access via `metadata.attachment_path`. Touches `frontmatter.py`.
  2. Leave it in the `extra` dict. `parse()` already collects unknown keys into `extra` (l. 108-111); `dumps()` re-emits them (l. 134). Zero `frontmatter.py` change. Access via `metadata.extra["attachment_path"]`. Less ergonomic, no validation.
  Both work; route (1) is preferred when other code reads the field structurally. Decision belongs to Brief #2 (capture pipeline produces the value).
- **Rename robustness**: when `report.pdf` is renamed/moved (Brief #3 sync), the sync mechanism updates `attachment_path` in the sibling's frontmatter. Sibling `vault_path` unchanged; `documents` row unchanged. Robust *if* sync fires.
- **Search hit semantics**: same as Option A — hit returns sibling. Tooling that needs the binary path follows the frontmatter pointer.

- **`source_file` overlap**: `NoteMetadata` already has `source_file: str | None`. Today's `_store_md` doesn't set it for non-md; today's `_store_nonmd` writes the binary name as the wikilink in the body, not a frontmatter field (l. 479). The existing `source_file` field could serve as the attachment pointer with no schema change — but its semantics in the existing capture flow ("the original source file before move") and its semantics here ("the attachment this sibling proxies") may overlap or conflict. Worth confirming in Brief #2; flagging here as a free-pre-existing-field that may be the cleanest landing for the pointer.

**Trade-off summary (no recommendation)**:

| Dimension | A (sibling-only) | B (attachment-only) | C (hybrid) |
|---|---|---|---|
| Schema change | None | DECISION-018 weaken or new column | None |
| Indexer change | dotfolder allowlist only | dotfolder allowlist + path rewrite + audit | dotfolder allowlist only |
| `frontmatter.py` change | None | None | Optional (Field vs extra) |
| Search-hit destination | sibling .md | PDF | sibling .md (+pointer to PDF) |
| Survives binary rename | row stable; link breaks | row breaks until renamed | row stable; pointer updated by sync |
| Attachment-query support (Phase 4+) | filesystem scan needed | direct query on `documents` | filesystem scan needed OR query on `source_file`/`attachment_path` |
| Aligns with DECISION-018 | Yes | No | Yes |
| Cost of OQ deferral | low | high | low |

### `vault/frontmatter.py` — adding `attachment_path` (or reusing `source_file`)

`_KNOWN_KEYS` (l. 27-42):

```python
_KNOWN_KEYS: frozenset[str] = frozenset({
    "type", "tags", "project", "domain",
    "created", "updated", "confidence", "updated_by_human",
    "summary", "source", "source_file", "status",
})
```

`NoteMetadata` (l. 45-72) Pydantic model has matching typed fields. Unknown keys are collected into `NoteMetadata.extra: dict[str, Any]` by `parse()` (l. 106-117). `dumps()` (l. 122-158) merges `metadata.model_dump(exclude_none=True, exclude={"extra"})` with `metadata.extra` before serializing — so anything in `extra` round-trips through write→read cycles.

`model_config = {"extra": "ignore"}` at l. 48 ensures unknown keys passed to the constructor (other than via the explicit `extra` argument) are silently dropped, which is fine because `parse()` separates them before instantiation.

Three concrete sub-options for the pointer (Option C above):

1. **Add typed field**: edit `_KNOWN_KEYS` + add `attachment_path: str | None = None` to `NoteMetadata`. Touches `frontmatter.py` and any test that asserts the frozen set. Most ergonomic.
2. **Use `extra` dict**: zero `frontmatter.py` change. Capture pipeline writes `extra={"attachment_path": "..."}`. Less discoverable.
3. **Reuse `source_file`**: zero `frontmatter.py` change. Set `source_file = "Projects/<A>/attachment/report.pdf"` (or relative `../report.pdf`). Semantic clash with existing intent ("source before move") — but for non-md captures the binary *is* the source.

None of these are blockers for Brief #1; flagging route choice to Brief #2 / plan stage.

### Storage layer interactions (no change)

`storage/documents.py::upsert(WriteOutcome)` (l. 60-102): writes `outcome.vault_path` verbatim. Whatever `to_vault_path(absolute_path)` returned from the writer is what the row carries. No path-shape assumption.

`storage/documents.py::_derive_title(outcome)` (l. 56-57): `outcome.metadata.extra.get("title")` else `Path(outcome.vault_path).stem`. For a sibling at `.summaries/report.md` the default title becomes `"report"`. If a richer title (e.g. AI-derived "Q1 Earnings Report") is desired, the capture pipeline must set `extra["title"]` before upsert. Not a layout concern, but worth flagging to Brief #2 since the boss-visible title (in search hits) depends on it.

`storage/documents.py::rename(old, new)` (l. 241-269): preserves integer id; FK references in `audit_log` / `corrections` survive. Used by the indexer when `detect_changes` reports a `moved` pair. Phase-3-relevant: when a binary is renamed and the sibling renames in step (Brief #3 sync), the indexer's `detect_changes` will collapse old→new sibling paths via `content_hash` match (the sibling body usually doesn't change just because the binary was renamed — but the wikilink inside it may; Brief #3's concern).

`storage/migrations/` — only `001_initial.sql` exists. No new migration needed for any option except B.

## Edge Cases & Silent Failure Modes

1. **Per-folder same-name collisions** — today's global `attachment/` made every basename globally unique. Two projects each containing `report.pdf` now legal. `documents.vault_path` UNIQUE still holds because the full path differs (`Projects/A/attachment/report.pdf` ≠ `Projects/B/attachment/report.pdf`). However, the collision loop in [pipelines/capture.py:459-468](../../pipelines/capture.py) currently iterates within a single `attachment_dir`; per-project that loop still works, but the global-uniqueness assumption (anywhere in code/tests/docs) is invalidated. Grep showed no other code relying on global uniqueness, but flagging for re-grep at plan time.

2. **`.summaries/` accidentally created under `inbox/`** — if a user drops a folder named `.summaries` into `inbox/` (unlikely but possible), `scan_vault` would now traverse it and index any `.md` inside. The allowlist exception is global; constraining it to "only inside `attachment/` subtrees" requires extra logic in the prune step. Two cures:
   - Accept and document: any `.summaries/<x>.md` anywhere in the vault is indexed.
   - Add a guard: `d == ".summaries"` allowed only when `dirpath.name == "attachment"`. Minimal extra code, robust. Recommend for plan.

3. **Atomic-write tmp file inside `.summaries/`** — `_atomic_write` (writer.py l. 87-103) writes `.tmp_<uuid>.md`. Inside `.summaries/`, the file is invisible because filename starts with `.`. On a `scan_vault` mid-write the tmp is skipped at l. 153. After `os.replace`, the target name `report.md` becomes visible. Safe.

4. **Cross-filesystem move** (writer.py l. 276-289) — when `src` is on a different filesystem than the new per-project `attachment/` (e.g. user dragged a file from an external drive into `inbox/`), `os.replace` raises EXDEV; the fallback writes `.tmp_<uuid><suffix>` next to `dst` (i.e. inside `Projects/<A>/attachment/`). The tmp is dot-prefixed (skipped by scans). After replace + unlink(src), tmp ownership transfers. Failure cleanup at l. 290-297 deletes the tmp. Safe in the new layout.

5. **`scan_non_md_drops` attachment-subtree skip** — today (l. 110-112): `if attachment_path in file_path.parents: continue`. Per-Project layout: there is no single `attachment_path`. If unchanged, this skip stops working — every binary already inside `Projects/<A>/attachment/` becomes a "drop" again on each scan loop, re-triggering capture. The skip must generalize to "any `attachment/` folder under `Projects/<*>/` or `Domain/<*>/`". This is Brief #2/#3 scope but **silent failure mode if missed**.

6. **Path.walk pruning + on-the-fly creation** — if the capture pipeline creates `Projects/<A>/attachment/.summaries/` *during* a scan loop, `Path.walk` may or may not see it depending on traversal order (implementation-defined). Practical impact: low (scans are fast; capture is event-driven), but a subsequent scan will pick it up. Not a correctness issue, just a delivery-latency note.

7. **NFC normalization on dotfolder name** — `.summaries` is ASCII; `unicodedata.normalize("NFC", ".summaries/report.md")` is idempotent. Vietnamese project names (e.g. `Projects/Phát-triển-A/`) NFC-compose tonal marks; `to_vault_path` already applies NFC. The sibling vault_path under that project normalises cleanly. Verified by reading the call site at `vault/paths.py:54`.

8. **Title default `"report"` for `Projects/A/attachment/.summaries/report.md`** — `documents._derive_title` falls back to `Path(vault_path).stem`. The boss's search results list "report" not "Q1 Earnings Report" unless the capture pipeline sets `extra["title"]`. Cross-cuts Brief #2.

## Dependencies & Coupling

- `vault/indexer.py` ← reads `vault/frontmatter.py`, `vault/reader.py`, `storage/documents.py`. Writes nothing.
- `vault/writer.py` ← reads `vault/frontmatter.py`, `vault/reader.py`, `vault/paths.py`. The only module allowed to call `Path.open("w")` or `.write_text()` (hook-enforced). `vault/paths.py` calls into CONFIG lazily.
- `vault/paths.py` ← lazy-imports `core.config.CONFIG` inside every function. Avoids the CONFIG-validates-vault-root-at-import trap (Cross-Phase Constraint).
- `core/config.py` ← used by `vault/paths.py`, `pipelines/capture.py`, `vault/indexer.py::scan_vault` (lazy import), `vault/watcher.py`. **If `attachment_path` property is removed (Option-VC-A) it breaks**: pipelines/capture.py l. 456 + l. 627, tests/test_core/test_config.py l. 353-355, tests/test_vault/test_watcher.py (multiple), docs/research/capture_pipeline.md (references — supersede), docs/roadmap.md l. 56+70 (supersede).
- `storage/documents.py` ← reads `vault.writer.WriteOutcome`. No path-shape assumption.
- `pipelines/capture.py` ← consumer; out of scope but coupled to every change above. Brief #2 handles.
- `vault/watcher.py` ← consumer; out of scope but takes `attachment_path` argument today. Brief #3 handles.
- `tests/test_vault/test_watcher.py`, `tests/test_core/test_config.py`, `tests/test_vault/test_writer.py` — directly reference `attachment_path` and `move_attachment`. Test updates land with the implementation.

## Extension Points

| Component | How extended | What blocks extension today |
|---|---|---|
| `vault/indexer.py` dotfolder prune | Add an entry to a `_DOT_ALLOWLIST: frozenset[str]` constant (new). | Today hard-coded `d.startswith(".")` filter — not data-driven. Migration: define `_DOT_ALLOWLIST = frozenset({".summaries"})` in indexer.py and gate the prune. Future allowlist entries (e.g. `.archive` if introduced) add to the frozenset only — no logic change. |
| `vault/indexer.py` `IGNORE_DIRS` | Already a frozenset — add a string. | No blocker. |
| `vault/paths.py` per-folder helpers | Add a function in the existing shape (lazy CONFIG, mkdir, return Path). | No blocker. Each new layout concept (`project_attachment`, `project_summaries`, …) is one function; no central dispatch to touch. |
| `core/config.py::VaultConfig` subdir names | Add a `Field` of type `str` with default; add matching `@property` if it's a top-level path. | No blocker. The CLAUDE.md guidance ("Field = human-configurable; @property = derived") applies cleanly: `summaries_subdir: str = ".summaries"` is a `Field`; `project_summaries(name)` lives in `vault/paths.py` as a helper (it depends on `name`, not just root). |
| `documents` schema additions (Option B or future attachments table) | New `.sql` file in `storage/migrations/`. DECISION-007 enforces versioned deltas — no in-code ALTER TABLE. | No blocker; just cost. Migration to a real `attachments` table is signposted by DECISION-018 ("would require a separate `attachments` table and a different indexer"). |
| `vault/frontmatter.py` new field (e.g. `attachment_path`) | Edit `_KNOWN_KEYS` + add a `Field` on `NoteMetadata`. | Not data-driven — code edit required. Alternative: leave in `extra` dict (zero edit). |
| Pointer from sibling to binary | Reuse `source_file` (no schema change) OR add `attachment_path` (typed) OR `extra["attachment_path"]` (untyped). | Each route works; choice is Brief #2's. |

**No extension-point regressions.** The proposed changes preserve every existing extension pattern: handlers still self-register, prompts still YAML, thresholds still config-driven, writes still gated by `vault/writer.py`.

---

## Open Questions

- **OQ-AL1**: `vault_path` for the sibling row — Option A (sibling-only), Option B (attachment-only, requires DECISION-018 change or schema change), or Option C (sibling + frontmatter pointer)? Surfaced trade-offs above; *no* recommendation per user instruction. Decision lands in `/plan revise_attachment_layout`.

- **OQ-AL2**: `core/config.py::VaultConfig::attachment_path` (l. 100) — remove the property entirely (Option-VC-A), repurpose it as a low-confidence staging area (Option-VC-B, depends on Brief #2 OQ-AC3 resolution), or keep as deprecated alias (Option-VC-C). I read all 7 call sites of `attachment_path` (3 non-test: capture.py:456, capture.py:627, config.py:100 definition; 4 test sites). No code outside pipelines/capture.py and tests/ uses it. The decision interacts with Brief #2 — flagging.

- **OQ-AL3**: Per-folder same-name PDF — global namespace gone. Searched code: no place outside `pipelines/capture.py:457-468` (the collision loop) assumes global basename uniqueness. `documents.vault_path UNIQUE` still works because full paths differ. **What I checked**: grep for `glob('*.pdf')`, grep for `attachment.iterdir`, grep for any path manipulation outside `vault/`, all came back empty. Confident this is a non-issue, but leaving as OQ for plan-time sanity grep.

- **OQ-AL4** (new, surfaced during research): should the `.summaries/` allowlist be global (any `.summaries/` directory anywhere) or scoped (only `.summaries/` directly inside an `attachment/` folder)? Edge Case #2 above. Scoped is safer (prevents accidental indexing of `inbox/.summaries/`); global is simpler. Implementation cost difference is ~3 lines. Decision belongs in plan.

- **OQ-AL5** (new, surfaced during research): Obsidian wikilink rendering inside `.summaries/report.md` pointing at `../report.pdf`. **What I checked**: read Obsidian's documented behavior is path-resolution relative to the note. The note is at `Projects/A/attachment/.summaries/report.md`; `[[report.pdf]]` resolves by Obsidian's vault-wide search (filename only), not relative path — so the wikilink should still work *if there is only one `report.pdf` in the vault*. With per-folder attachment, two projects both having `report.pdf` will make `[[report.pdf]]` ambiguous. Obsidian disambiguates via full path: `[[Projects/A/attachment/report.pdf]]`. Capture pipeline must emit a full-path wikilink or relative path. **Cannot verify live in research session** — flagging for plan to test on a real vault.

## Reference Project Patterns

`grep -i attachment .docs/reference/knowledge-base-server/...` from prior research (per `docs/research/vault_layer.md` § "Reference Project Patterns") shows the reference project does NOT split attachments per-folder; it uses a flat `_assets/` style global folder. **Reason in the reference project**: their use case is one-vault-one-user-one-project; per-folder attachments add no value. **Reason it does not apply here**: the user explicitly modeled the layout on her boss's mental model ("everything for project A in one place"). The reference pattern is rejected with stated reasoning. No cargo-culting.

The reference's indexer (per `docs/research/vault_layer.md` § "Reference Project Patterns") uses ad-hoc dotfolder skip without an allowlist. Our codebase already diverges by having `IGNORE_DIRS` as a frozenset. The `.summaries/` allowlist is a small extension of an existing data-driven structure — pattern-aligned, not invented.

## Technical Debt Spotted

- **TD-RAL-1**: `docs/research/capture_pipeline.md` § "Non-md branch (PDF, DOCX)" (l. 337-372) documents the OLD layout (`sibling = src.parent / ...`, single global attachment_path, wikilink `[[{attachment_dst.name}]]`). After Brief #2 is built, that section is superseded; either annotate or rewrite. Recommend annotation now ("→ see `revise_attachment_layout.md` for new layout") to avoid silent staleness for future readers. _Owned by_: Brief #2 / plan stage.

- **TD-RAL-2**: `docs/roadmap.md` Phase 1 (l. 53-66) describes the OLD layout in detail (sibling-next-to-source, single attachment/). The boss-demo work depends on the revised layout; the roadmap text needs an update or annotation. _Owned by_: post-plan documentation pass.

- **TD-RAL-3**: `docs/phase_1_detailed_specs.md` l. 35 + l. 232-234 reference `move_attachment(src, attachment/<name>)` in the OLD layout's shape. Same supersession issue. _Owned by_: post-plan documentation pass.

- **TD-RAL-4**: `vault/watcher.py` takes a single `attachment_path` arg (l. 37-72, l. 147-167) used to skip events. Per-project layout makes that arg insufficient. Flagged for Brief #3; not blocking Brief #1's outputs.

- **TD-RAL-5**: `tests/test_core/test_config.py:353-355` asserts `vault.attachment_path == tmp_path / "attachment"`. If `attachment_path` property is removed (Option-VC-A), this test deletes; if repurposed (Option-VC-B/C), this test updates. Carry into plan.

## Downstream Phase Impact

- **Brief #2 (`attachment_capture_pipeline`)**: consumes (a) the new `vault/paths.py` helpers (`project_attachment(name)`, `project_summaries(name)`, etc.), (b) the OQ-AL2 resolution for what `attachment_path` config means, (c) the OQ-AL1 resolution for what `vault_path` the row carries, (d) the OQ-AL5 wikilink path shape decision.
- **Brief #3 (`attachment_sync_and_archive`)**: consumes (a) the `.summaries/` traversal allowlist (so `scan_vault` finds the siblings whose attachments need sync-checking), (b) the OQ-AL1 row-shape decision (sibling-vs-attachment sync targets differ), (c) the per-Project/Domain `attachment/` enumeration logic (`scan_non_md_drops` generalization).
- **Phase 3 (Retrieval, Roadmap)**: embedding source is the sibling content under Options A/C, which is coherent — embeddings are computed from `.md` bodies. Under Option B, the embedding source must be re-derived (read sibling content despite `vault_path` pointing at binary) — extra coupling.
- **Phase 4 (MCP MVP)**: `kms_search` returns `documents` rows. Under A/C the boss clicks → opens the sibling summary (one extra click to view source). Under B the boss clicks → opens the PDF (no preview, harder to know if it is the right file). UX trade-off, not a correctness issue.

## Self-review notes

- **Unsupported claims**: re-read for hedging. Every claim about file behavior cites a line range or grep result. The Obsidian wikilink behavior (OQ-AL5) is the one place I cannot verify in the research session — explicitly marked as such.
- **Gaps disguised as confidence**: `vault/watcher.py` is Brief #3 scope; I read just enough (grep on `attachment_path` references) to confirm it takes the arg and to flag TD-RAL-4. Not deep-traced; called out.
- **Missing downstream impact**: added section above.
- **Contradictions with existing research**: `docs/research/capture_pipeline.md` and `docs/roadmap.md` describe the OLD layout. This file explicitly supersedes; not a true contradiction, but flagged at the top and in TD.
- **Cargo-culted patterns**: explicit reasoning for not adopting the reference's flat `_assets/` folder.

---

✅ Research complete → docs/research/revise_attachment_layout.md
Open questions remaining: 5 (OQ-AL1 through OQ-AL5)
Ready for: /plan revise_attachment_layout
