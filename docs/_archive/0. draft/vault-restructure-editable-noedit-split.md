# Draft — Vault Restructure: Editable vs No-Edit File Split

**Status:** Draft for next-session design work. NOT a spec, NOT a plan. Captured from a grilling session on 2026-06-03.
**Author handoff:** This document is the input for a design session with another AI. It records *what* must change and *why*, with enough code-grounded diagnosis to design the *how*. Decisions below were confirmed one-by-one with the user; treat them as settled requirements unless the design surfaces a contradiction.

---

## Background — why this exists

The target user is a non-technical executive whose real working files are **office documents** (xlsx, docx, pptx) and **reference files** (pdf, images). Two findings forced a redesign:

1. **They will not work with `.md` files.** Their day-to-day artifacts are office files. The system must keep those files where the user can see and open them.
2. **The current pipeline hides every captured binary inside `Projects/<A>/attachment/`** (a folder Obsidian hides). That's fine for read-only references (pdf, images) but wrong for files the user actively edits — they vanish from view.

**Core decision:** split captured binaries into two classes and route them to different homes.

- **Editable** (docx, xlsx, pptx, …) → live in the **project/domain root**, visible.
- **No-edit** (pdf, images) → live in **`attachment/`**, hidden.

Everything else in this document follows from that split or fixes adjacent breakage discovered while investigating it.

### How we got here (diagnosis already done)
A live `kms watch` repro + temporary event logging confirmed the current behavior:
- A binary dropped/moved **into `Projects/<A>/attachment/`** is silently skipped by the watcher (`_should_skip` managed-attachment rule) → no capture.
- A binary dropped into a **project root** *does* capture, but the pipeline then **moves it into `attachment/`** — exactly the behavior we now want to stop for editable files.
- The pipeline's own binary move is **seen by the watcher as a move event** (relevant to Task 8).

---

## Canonical vocabulary (use these terms consistently)

- **No-edit file** — a non-`.md` file whose extension is in the config list `no_edit_extensions` (pdf + images). Routed to `attachment/`.
- **Editable file** — any non-`.md` file *not* in `no_edit_extensions`. Routed to the project/domain root.
- **Sibling** — the AI-written `.md` summary for a binary. Lives at `<binary's parent>/.summaries/<binary.name>.md` (next-to-binary rule, unchanged).
- **AI-output folder** — `Briefings/`, `Synthesis/`, `Documentation/`. AI writes here; users never drop source here. **Capture-excluded.**
- **Misplaced location** — any folder that is NOT one of {`inbox/`, a specific `Projects/<A>/`(+its `attachment/`), a specific `Domain/<D>/`(+its `attachment/`)} and is NOT an AI-output folder. E.g. bare `Projects/`, bare `Domain/`, `Domain/<D>/Archive/`, vault root. A file here is a user mis-drop → moved to inbox.
- **Re-home** — on a *user* move of a binary, relocate its sibling + re-derive location tags + move the binary per the type rule, **reusing the existing summary** (no LLM). Distinct from re-capture.
- **Re-capture** — full pipeline re-run incl. LLM summarization. Triggered only by a **content change**, never by a move.

---

## Platform scope

**Mac-first.** Design the change-detection layer platform-agnostic, but only probe/verify/tune on macOS for the June demo. Windows verification is deferred (see TD-W1).

---

# Tasks

Each task below is a candidate unit of work. The next session should decide grouping, ordering, and the actual implementation approach.

---

## T1 — Config: `no_edit_extensions` + capture-excluded folders

**Description/requirements**
Two new config-driven lists in `src/config/config.yaml`:
- `no_edit_extensions` — the canonical list (the *smaller, more stable* set): pdf + image extensions. Anything non-`.md` not in this list is editable.
- A capture-excluded folder list (AI-output zones): `Briefings/`, `Synthesis/`, `Documentation/`.

**Goals**
- Make the editable/no-edit boundary data, not code (Extension Point Rule).
- Make the AI-output exclusion explicit and config-driven (parallel to `IGNORE_DIRS`).

**Anti-goals**
- No hardcoded extension lists in pipeline/handler/watcher code.
- Do not define *both* an editable list and a no-edit list — one canonical list only (avoids a file matching neither/both).

**Scope**
- IN: config keys + their Pydantic fields on the relevant config model.
- OUT: the logic that consumes them (that's T2/T4).

**Done when**
- `no_edit_extensions` and the AI-output folder list load and validate via the config singleton.
- A test reads both back from config.

**Open questions / tech debt**
- Exact image extension set to ship with: candidate `.png .jpg .jpeg .gif .webp` — confirm whether `.heic .tiff .svg .bmp` belong.

**Other / file diagnosis**
- `src/config/config.yaml` — `vault:` block at L69; existing `cooldown_seconds` L2, `folder_cooldown_seconds` L7.
- `src/core/config.py` — `VaultConfig` already holds `attachment_dir`, `summaries_subdir`, `projects_path`, `domain_path`, `inbox_path`, `archive_dir`. Decide whether the new lists hang off `VaultConfig` or a capture/classify config block. Existing `IGNORE_DIRS` lives in `src/vault/indexer.py:42` as a module frozenset — decide if the AI-output list should join/mirror it or stay separate in config (it's user-meaningful, so config is preferred).

---

## T2 — Single shared placement helper

**Description/requirements**
One function that decides where a file physically goes, given `(file, target_type, target_name)` → returns `(final_binary_path, sibling_path)` using `no_edit_extensions`. The **only** copy of the editable/no-edit routing rule. Called by capture today and by Phase 2 Classify later.

**Goals**
- Single source of truth so capture and the future Phase 2 Classify cannot drift apart.
- Encode the symmetric, type-driven decision (see T3) in one place.

**Anti-goals**
- No duplicate placement logic inside `_store_nonmd` and (later) the classify pipeline.
- No LLM in this helper — pure path math.

**Scope**
- IN: the helper + unit tests for each branch (editable/no-edit × in-attachment/in-root/elsewhere).
- OUT: rewiring `_store_nonmd` to call it (that's T3); Phase 2 (not built).

**Done when**
- Helper returns correct binary + sibling paths for all branches.
- `_store_nonmd` (T3) consumes it; no second copy of the rule exists.

**Open questions / tech debt**
- Where it lives: `src/vault/paths.py` (already the parametrized path home: `project_attachment`, `project_summaries`, `domain_attachment`, `domain_summaries` at L207–246) is the natural fit. Confirm.

**Other / file diagnosis**
- `src/vault/paths.py` — has `project_attachment(name)`, `project_summaries(name)`, `domain_attachment(name)`, `domain_summaries(name)`. The helper composes these + a root path + the no-edit check.
- Sibling naming rule is fixed: `<binary.name>.md` (full filename incl. extension), see `_sibling_for` in `src/vault/watcher.py:54` and `_store_nonmd` sibling write at `src/pipelines/capture.py:630-633`. Reuse the same naming.

---

## T3 — `_store_nonmd`: symmetric, type-driven `needs_move`

**Description/requirements**
Rework destination resolution so binary placement follows the type rule:
- no-edit + not already in `attachment/` → move **into** `attachment/`
- editable + currently in `attachment/` → move **out** to project/domain root
- otherwise → stay
And the sibling always follows the binary's *final* parent (`<final parent>/.summaries/`).

**Goals**
- Editable files end up in the project/domain root with their sibling in root `.summaries/`.
- No-edit files keep current behavior (into `attachment/`, sibling in `attachment/.summaries/`).

**Anti-goals**
- Do not move editable files into `attachment/` (the current bug).
- Do not change `.md` handling (`_store_md` stays in-place except for the misplaced rule, T4).

**Scope**
- IN: `_store_nonmd` destination resolution + sibling-dir selection; tests for both file classes in both root and attachment starts.
- OUT: the misplaced→inbox relocation (T4); the shared helper extraction (T2 — this task consumes it).

**Done when**
- Dropping an editable file in a project root → stays in root, sibling in root `.summaries/`, DB row points at the root binary.
- Dropping a no-edit file in a project root → moved into `attachment/`, sibling in `attachment/.summaries/`.
- An editable file *encountered in* `attachment/` during capture → pulled out to root.

**Open questions / tech debt**
- Collision handling on the *root* destination for editable files mirrors the current attachment collision loop (`-1`, `-2`, … up to 100) at `src/pipelines/capture.py:600-611`. Confirm same policy in root.

**Other / file diagnosis**
- `src/pipelines/capture.py:540-697` (`_store_nonmd`). Current resolution logic L561-575 sets `needs_move = rel.parts[1] != attachment_dir` for any project/domain file — this is exactly what becomes type-driven.
- Sibling-first ordering (DECISION-025) at L630-654 (write sibling) then L656-671 (move binary). Preserve ordering; only the *destination* changes.
- `source_hash` computed at L638-639 — keep; it's the anchor for content-change detection (T9).
- Sibling metadata sets `type=attachment-summary` (L640-649) — KEEP for editable-in-root siblings too; DECISION-029 + reconcile Stage 4 depend on this type guard even outside `attachment/`.

---

## T4 — Misplaced→inbox (all types) + AI-output capture-exclusion

**Description/requirements**
Two folder classes, both new behavior:
1. **AI-output folders** (`Briefings/`, `Synthesis/`, `Documentation/`) → watcher `_should_skip` and `scan_capture` skip them entirely. Never captured, never evicted.
2. **Misplaced locations** → any file dropped there (incl. `.md`) is moved to `inbox/` and processed as a normal inbox drop (CLUELESS / pending-routing for non-md; in-place-in-inbox for md).

**Goals**
- AI's own outputs never get re-captured (feedback-loop prevention).
- User mis-drops land in inbox where the system can route them, regardless of file type.

**Anti-goals**
- Do NOT evict files from valid `Projects/<A>/` or `Domain/<D>/` roots — those are legitimate homes (editable files, notes).
- Do NOT relocate AI-output files (would move a briefing to inbox → re-capture loop).

**Scope**
- IN: capture-exclusion check (watcher + scan_capture); misplaced detection + move-to-inbox for md and non-md.
- OUT: how inbox files get *routed onward* (Phase 2 Classify, not built).

**Done when**
- A file dropped in `Briefings/` is ignored by watcher + scan_capture.
- A `.docx` or `.md` dropped in bare `Projects/` (no project subfolder) is moved to inbox.
- A `.md` in a valid `Projects/<A>/` root is left alone.

**Open questions / tech debt**
- **OQ-008 (already logged):** capture-excluding AI-output folders blinds the system to *human edits* of those files during the future co-authoring phase. Needs an edit-detection path that flags `updated_by_human` without triggering a capture. Revisit at co-author design.
- Define the misplaced-location predicate precisely against the existing `_location_context` (`src/vault/paths.py:87`) which currently returns `(None, None)` for unmatched paths and treats `Projects/<file>` as `("project", "<filename>")` — that quirk (L117-121) must be handled so a bare-`Projects/` file isn't mistaken for a project named after the file.

**Other / file diagnosis**
- Today's partial behavior: non-md in bare `Projects/`/`Domain/` already routes CLUELESS→inbox via `_store_nonmd` (`src/pipelines/capture.py:566-575,699-715`) **because** `len(rel.parts) < 2` leaves `target_type=None`. Md files do NOT (handled by `_store_md`, written in place). This task makes md consistent.
- `_should_skip` at `src/vault/watcher.py:124-141`. `scan_capture` skip logic and its `.summaries/` skip are in `src/pipelines/capture.py` (`scan_capture` def at L918). Existing `IGNORE_DIRS` at `src/vault/indexer.py:42`; `_DOT_ALLOWLIST = {.summaries}` at L56; `IGNORE_FILES` incl. `CLAUDE.md` at L60.

---

## T5 — `capture_folder`: exclude `attachment/` + `.summaries/` by name

**Description/requirements**
`_collect_folder_files` must skip any file whose path (relative to the dropped folder) contains a part equal to the configured `attachment_dir` or `summaries_subdir`. Exclusion is by **name**, not by the full managed-attachment predicate.

**Goals**
- Folder batch `file_count` reflects only real capturables → accurate PARTIAL/COMPLETE status.
- Close a re-capture hole: today sibling `.md` files inside `.summaries/` get collected and re-captured (wiping `attachment_path` — same bug class as TD-AS-1).

**Anti-goals**
- Do not use the grandparent-based managed predicate here (awkward mid-`rglob`, and name-exclusion matches `scan_capture`'s existing behavior).

**Scope**
- IN: `_collect_folder_files` skip rule + test (folder containing `attachment/` and `.summaries/` subdirs).
- OUT: changing how individual files capture.

**Done when**
- A folder drop containing an `attachment/` subtree and `.summaries/` siblings collects neither.
- Batch `file_count` excludes them.

**Open questions / tech debt**
- Minor over-exclusion: a user folder literally containing a subdir named `attachment` would be skipped. Acceptable for the target user; note it.

**Other / file diagnosis**
- `_collect_folder_files` at `src/pipelines/capture.py:1087-1104`. Current skips: dirs, dotfile *names* only (not parent dirs), `IGNORE_DIRS` parts. The dotfile check (`p.name.startswith(".")`, L1098) does NOT catch `.summaries/report.pdf.md` because the *file* name isn't dotted. Add `attachment_dir`/`summaries_subdir` name checks against `rel_parts`.
- `capture_folder` at L1230; batch insert + `file_count` at L1266-1268 / L1307-1310 / L1331-1334.

---

## T6 — Watcher: re-home on user move

**Description/requirements**
On a genuine user move of a binary, the watcher must **re-home** it: recreate the sibling at the destination `.summaries/`, re-derive only the project/domain location tags, move the binary per the type rule (T2/T3), **reusing the existing summary** — no LLM call. Replaces the current orphan-old-sibling-and-do-nothing behavior for cross-folder moves.

**Goals**
- A manually-moved binary ends up correctly homed (right folder per type, sibling present, tags + DB updated) with zero LLM cost.
- Summary survives the move.

**Anti-goals**
- No re-summarization on a move (content unchanged). Re-capture is content-change-only (T9).
- Do not read the summary from the on-disk source sibling (it may not exist after a coalesced move chain — see T7). Use the DB.

**Scope**
- IN: re-home logic in `_handle_binary_move` (or its successor); summary lookup via DB; sibling recreation; location-tag re-derivation; type-driven binary move.
- OUT: move-chain convergence (T7), sticky-note suppression (T8) — but this task must be designed to compose with both.

**Done when**
- `Projects/A/report.pdf` → moved to `Projects/B/` → sibling appears at `Projects/B/attachment/.summaries/` (no-edit) or `Projects/B/.summaries/` (editable), summary preserved, DB row updated, domain/project tags corrected, old sibling removed.

**Open questions / tech debt**
- Summary lookup key: find the existing sibling row by the binary's prior indexed path / identity. Confirm `documents` schema supports a clean reverse lookup (recent commits added `project`, `status`, `key_topics` to `DocumentRow`; `attachment_path` lives in sibling frontmatter, not necessarily a queryable column — verify).
- Re-deriving location tags reuses `apply_location_tags` (`src/pipelines/capture.py:277-316`) logic — decide whether to call it or a slimmer extraction.

**Other / file diagnosis**
- `_handle_binary_move` at `src/vault/watcher.py:344-449`. Same-folder branch (L357-419) renames sibling + updates `attachment_path` pointer + renames DB row. Cross-folder branch (L420-449) currently just orphans the old sibling — this is what becomes re-home.
- `on_moved` dispatch at `src/vault/watcher.py:280-299`; binary sync debounced under `bin:{dst}` key (L289-291). `on_move` callback (DB rename only) wired in `src/cli/main.py:241-251`.
- Module-attribute monkeypatch note (TD-033 / CLAUDE.md): tests patch `vault.watcher.<name>` not the source module — preserve.

---

## T7 — Move-chain convergence (settle window)

**Description/requirements**
Successive moves (`A→B→C`, the "dropped wrong, fixed it" pattern) must converge to a **single** re-home at the **final** location — no intermediate sibling churn, no lost summary, no duplicate rows.

**Goals**
- Only the final resting place triggers a re-home.
- Robust to moves that happen faster than the debounce window.

**Anti-goals**
- No per-hop re-home (creates and orphans intermediate siblings).
- No reliance on the immediate `src` having an on-disk sibling.

**Scope**
- IN: a settle/cooldown window for binary moves, keyed on a **stable binary identity** (filename or DB row), mirroring the existing folder-cooldown pattern; DB-based summary lookup so coalescing doesn't lose the summary.
- OUT: the re-home mechanics themselves (T6).

**Done when**
- `A→B→C` within the settle window produces exactly one sibling (at C), one DB update, correct tags, summary intact.

**Open questions / tech debt**
- Identity key choice: filename coalesces moves of the same file but collides if two different same-named files move concurrently. For a single sequential human user this is acceptable; confirm. Content-hash keying avoids collisions but costs a read per event.
- Reuse `folder_cooldown_seconds` or add a `binary_settle_seconds` config? Recommend a dedicated key.

**Other / file diagnosis**
- Existing cooldown machinery to mirror: `_register_pending_folder` / `_reset_folder_timer` / `_fire_folder_stable` with the token-based stale-fire guard at `src/vault/watcher.py:182-249`. The token guard (C2 fix) is the pattern to copy for "a later move supersedes an earlier pending re-home."
- `_debounce` at L151-159 cancels-and-restarts per key — note that two `_debounce` calls with the *same* key cancel each other (CLAUDE.md), and different keys do NOT coalesce. That's why a stable identity key is required.

---

## T8 — Sticky-note: suppress pipeline-initiated moves

**Description/requirements**
The watcher must not re-home a binary that the **pipeline itself** just moved (e.g. no-edit binary moved root→`attachment/` during capture). Before the pipeline moves a binary, it registers the path(s) in a short-lived, **TTL-expiring** suppression registry; the watcher's move handler checks the registry and skips re-home for registered paths.

**Goals**
- No "watcher fighting the pipeline" — the pipeline's own moves (which already did all bookkeeping) are ignored by the re-home path.
- A user move (no registration → bookkeeping not done) is still handled.

**Anti-goals**
- Do not permanently deafen the watcher — entries must expire so a crash mid-move can't leave a path suppressed forever.
- Do not rely solely on "already in DB" (racy; can't distinguish AI-just-moved from user-moved-an-indexed-file).

**Scope**
- IN: a small TTL registry (register before move, check + auto-expire in the watcher); wiring at every pipeline binary-move site.
- OUT: re-home mechanics (T6).

**Done when**
- Capturing a no-edit binary in a project root (pipeline moves it into `attachment/`) triggers **no** re-home.
- A user-initiated move of the same binary **does** trigger re-home.

**Open questions / tech debt**
- Registry scope/lifetime: in-process set with timestamps, or piggyback on an existing structure? The watcher and pipeline run in the same process under `kms watch` (pipeline submitted to a thread pool, `src/cli/main.py` + `src/vault/watcher.py:478-490`) — confirm shared-memory access is clean across the observer thread and the asyncio loop / thread pool.
- TTL value: a few seconds; tie to debounce/settle so it outlives the event-delivery delay but not longer.

**Other / file diagnosis**
- Pipeline binary-move sites: `move_attachment` calls in `_store_nonmd` (`src/pipelines/capture.py:658`, and inbox-park move L711). Folder moves go through `move_folder` (L1297). The registry must wrap the *binary* moves at minimum.
- Watcher move entry: `src/vault/watcher.py:280` `on_moved`; binary sync at L288-291.

---

## T9 — Content-change detection on binary edit (atomic-save aware)

**Description/requirements**
Detect when a binary's **content** changes and re-capture (re-summarize + update sibling). Detection must work however the OS surfaces the edit — a clean `modify`, OR an **atomic save** (write temp → delete original → rename temp over it) that surfaces as delete+create/move within a debounce window. Compare the binary's current hash against the sibling's stored `source_hash`; mismatch → re-capture; match → no-op.

**Goals**
- Editable files (now living in the open, edited often) keep their sibling summary + index fresh.
- No spurious re-capture when content is unchanged.

**Anti-goals**
- Do not let the **delete half** of an atomic save orphan the sibling (current `_handle_binary_delete` would).
- Do not re-capture on pure moves (T6/T7 handle those).

**Scope**
- IN: binary content-change detection + hash compare against `source_hash`; re-capture trigger; protection of the sibling against atomic-save delete.
- OUT: Windows-specific handling (TD-039).

**Done when**
- Editing a docx in a project root and saving (via the app's real save path) refreshes its sibling summary and `source_hash`; the sibling is not orphaned mid-save.
- Saving with no content change is a no-op.

**Open questions / tech debt**
- **TD-037** (binary-modify re-capture) — currently deferred; this task delivers it. `on_modified` returns early for non-md at `src/vault/watcher.py:257-260` ("Binary modify deferred — TD-C6").
- **Central design problem:** the exact macOS event sequence for Word/Excel/PowerPoint atomic saves. **Run a real-vault probe first** (open, edit, save each office type under `kms watch`; capture the create/move/delete sequence) before designing the handler. Do not design blind.
- `_handle_binary_delete` (`src/vault/watcher.py:303-342`) orphans the sibling on any binary delete — must be reconciled with the atomic-save delete half (e.g. defer sibling deletion if a matching create/rename arrives within a window).
- Reverse lookup binary→sibling is deterministic via `_sibling_for(binary)` (`src/vault/watcher.py:54`), so the hash compare is cheap; the hard part is the event pattern, not the comparison.

**Other / file diagnosis**
- `source_hash` is written into sibling frontmatter at capture (`src/pipelines/capture.py:638-649`) and already used by `capture_file`'s idempotent guard (L868-908) — reuse that comparison logic.
- `on_modified` at `src/vault/watcher.py:251-260`; `on_deleted` at L262-278; `on_created` at L161-178. All three may participate in atomic-save detection.

---

## T10 — Reconcile migration stage (existing editable-in-attachment)

**Description/requirements**
A new `kms reconcile` stage that sweeps the vault for **editable files already sitting in `attachment/`** (from the old pipeline) and pulls them out to the project/domain root, moving their sibling to root `.summaries/` and updating the DB. Heals existing vaults and any future drift.

**Goals**
- Existing editable files become visible in the root without manual work.
- One on-demand command; no separate throwaway script.

**Anti-goals**
- Not a capture-time fix (T3's symmetric `needs_move` only runs on files going *through* capture; already-captured files never re-enter capture, so they need an active sweep — these are complementary, not redundant).

**Scope**
- IN: a reconcile stage that finds + relocates editable-in-attachment files (binary + sibling + DB).
- OUT: live capture behavior (T3).

**Done when**
- Running `kms reconcile` on a vault with a `.docx` in `Projects/A/attachment/` moves it to `Projects/A/`, moves its sibling to `Projects/A/.summaries/`, updates the DB row, leaves no orphan.

**Open questions / tech debt**
- Confirm the existing reconcile command structure and where a stage plugs in. Brief #3 Phase 4 added a 4-stage reconcile (paths, orphan binaries, stale binaries, orphan siblings). This is a natural Stage 5 or an extension of an existing stage.
- Low stakes today (pre-deployment, test data only) but the long-term home for drift correction.

**Other / file diagnosis**
- Reconcile lives in the CLI + a reconcile module (search `kms reconcile` / `reconcile_` in `src/cli/main.py` and `src/`). Reuses `_is_in_managed_attachment` (`src/vault/paths.py:26`) to scope the walk and `_is_managed_summaries_area` (L56) for sibling areas.
- Must use the T2 shared placement helper so migration and capture agree.

---

## T11 — Bug: `_handle_binary_move` orphan-branch correlation_id — LIKELY ALREADY FIXED, VERIFY

**Description/requirements**
Originally observed: the orphan branch of `_handle_binary_move` wrote an audit row without a bound correlation_id, dropped with `error=missing correlation_id` (logs, 2026-06-03 09:08). **However**, the uncommitted working-tree edit on branch `fix/phase1.5-codereview` already adds `bind_contextvars(correlation_id=new_correlation_id())` at the **top** of `_handle_binary_move` (and `_handle_binary_delete`), which lexically encloses the orphan branch. Logs at 09:25 (after the watcher was restarted to pick up that edit) show a correlation_id present on the orphan path. So this is **probably already resolved** — the 09:08 failure pre-dated the edit.

**Done when**
- Confirm via a cross-folder binary-move test that the orphan-branch audit row is written with a correlation_id; no `missing correlation_id` warning. If confirmed, close with no code change.

**Anti-goals**
- Do not re-add a second bind or restructure — the fix (if needed at all) is already present.

**Other / file diagnosis**
- `src/vault/watcher.py` — function-top bind at L344-347 (in the uncommitted diff); orphan-branch audit_write at L435-449 inherits it. These watcher.py edits were already in the working tree at session start (`git status: M src/vault/watcher.py`) — they are NOT part of this draft's scope, just context.
- A threading nuance to check: `_handle_binary_move` runs in a `threading.Timer` thread (debounced). `bind_contextvars` sets the contextvar in that thread's context — verify the audit_write reads it in the same thread (it does, synchronously). **Folded into T6** since T6 rewrites this function anyway; T6 must preserve the bind.

---

# Cross-cutting concerns

- **Phase 2 Classify (not built) must call the T2 helper** for inbox files it routes onward. Inbox drops currently park as CLUELESS / pending-routing (`src/pipelines/capture.py:699-763`); Phase 2 resolves the marker and files the binary — it must use the same placement rule, or editable inbox files land in `attachment/`.
- **Type guard preservation:** any code writing into a `.summaries/` dir (now incl. root `.summaries/`) must set `type=attachment-summary` (DECISION-029). Reconcile's orphan-sibling stage requires it.
- **The two near-twin path predicates** (`_is_in_managed_attachment` vs `_is_managed_summaries_area`, `src/vault/paths.py:26` & L56) now must also recognize the **root-level `.summaries/`** as a managed summaries area. CLAUDE.md flags these as a silent-bug hotspot — touch carefully.

---

# Consolidated open questions / tech debt

- **OQ-008 (logged in OPEN_QUESTIONS.md):** human edits to capture-excluded AI-output folders during co-authoring — how to flag `updated_by_human` without triggering capture. Revisit at co-author design. Related: OQ-005, OQ-002.
- **TD-037 (existing):** binary-modify re-capture — delivered by T9.
- **TD-039 (logged in TECH_DEBT.md):** Windows support for content-change detection + atomic-save handling. Needs: real-vault event probe on Windows Office, a read-when-unlocked retry path (Windows holds an exclusive lock on open office files), and `~WRD*.tmp` / `~$*` temp-file ignore patterns. Blocks any Windows user; not the June demo.
- Image extension set for `no_edit_extensions` (T1).
- `documents` reverse-lookup support for re-home summary carry (T6).
- Binary-move identity key: filename vs content-hash (T7).
- Suppression registry cross-thread access (T8).

---

# Suggested reading order for the next AI

1. `STATE.md`, `CONSTRAINTS.md`, `CLAUDE.md` "What Claude gets wrong" section (vault path gotchas).
2. `src/vault/watcher.py` (whole file — small, central to T6/T7/T8/T9/T11).
3. `src/pipelines/capture.py` — `_store_nonmd` (L540), `capture_file` (L793), `capture_folder` (L1230), `_collect_folder_files` (L1087), `apply_location_tags` (L277).
4. `src/vault/paths.py` — the predicates (L26, L56) and parametrized path helpers (L165-291).
5. `src/cli/main.py` — `watch` (L145) wiring.
6. `src/vault/indexer.py` — `IGNORE_DIRS` / `_DOT_ALLOWLIST` / `IGNORE_FILES` (L42-60).

---

# Suggested build order (for discussion, not fixed)

**Hard dependencies (fixed):** T1 → T2 → T3; T3 → T8 → T6 → T7; T2/T3 → T10.
T8-before-T6 is mandatory (re-home must not fire on the pipeline's own moves). T8 is infrastructure only — **never ship T6 without T8 in the same increment.** T11 (audit correlation_id) folds into T6 and is likely already fixed (verify only).

**Order — T9 is deferred; everything else proceeds now.** T9 (content-change) is the ONLY probe-gated task. The probe needs the user present (open/edit/save Office files), so build the entire dependency chain first and slot T9 in after the probe is run.

1. **Foundation (independent, low-risk):**
   - **T5** (folder collect exclude-by-name) — standalone, dependency-free bug fix; closes the `.summaries/` re-capture hole. Lead with it as a safe quick win.
   - **T1** (config: `no_edit_extensions` + AI-output list).
   - **T4** (misplaced→inbox + AI-output capture-exclusion) — only needs T1's AI-output list, no T2 dependency; can pair with T1.

2. **Routing change (the headline value):**
   - **T2** (shared placement helper) → **T3** (`_store_nonmd` editable-in-root). Testable end-to-end via capture. After this, editable files stay visible — the user's core ask.

3. **Move story (no probe needed — do now):**
   - **T8** (sticky-note) → **T6** (re-home, T11 folds in) → **T7** (move-chain). An Office save surfaces as delete+create, not a move, so this path is independent of T9 — building it before T9 creates no rework.

4. **T10** (reconcile migration) — once placement rules (T2/T3) are final. Can run any time after step 2; not gated on the move story or T9.

5. **DEFERRED — needs the user + a probe:**
   - **Atomic-save PROBE** (zero code): open/edit/save docx + xlsx + pptx under `kms watch` on macOS; record the exact create/move/delete event sequence.
   - **T9** (content-change / edit refresh) — design + build the handler from the probe results. Do NOT design blind.

**Interim gap until T9 lands:** editing an editable file in place will not refresh its sibling summary. This is the *current* behavior already (binary edits are a no-op today, `watcher.py:257-260`), so it is not a regression — just an unclosed gap noted for the user.

**Rationale for the changes from the first draft:** T5 promoted to lead (isolated + safe); T4 paired with T1 (no T2 dep); T9 isolated as the sole deferred, probe-gated task (user is away and the probe needs them) while the move story T6/T7/T8 — which does NOT need the probe — proceeds in this session's chain.
