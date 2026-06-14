# Tech Debt

## Active

### TD-062 · Reconstruct lost P6-A1 behavior-inventory entries
**Status:** OPEN
**Phase:** Phase 6 — before the next `update-behavior-guide` / testing-guide regeneration.
**Risk if triggered early:** None to code; the A1 daemon code is recovered + merged and tests pass. The gap is in the behavior inventory only: the A1 design doc claims ~22 `P6-A1-*` entries, but **zero are present in `docs/system_behavior/behavior_inventory.yaml`** — they were lost when the A1 worktree branch was deleted (same orphaning as the merge recovery). If the testing guide is regenerated now, A1's behaviors are silently absent from it.
**What:** Reconstruct the ~22 `P6-A1-*` behavior entries from `docs/1_design/phase6/P6_slice_A1_daemon_core.md` + `docs/2_specs/phase6/P6_slice_A1_daemon_core.md` (Behavior IDs P6-A1-01..22) back into `behavior_inventory.yaml`, then regenerate the testing guide. A2 entries (`P6-A2-01..09`) are already present and unaffected.
**Why deferred:** Owner decision (2026-06-14) — A2 design→spec→research→plan doesn't need them; reconstruct at the testing-guide regeneration step to keep A2 momentum.
**Source:** Phase 6 Slice A2 build-pipeline design review, 2026-06-14 (gap surfaced by the design-lite subagent while appending P6-A2 entries).

---

### TD-061 · OneDrive Files On-Demand placeholder support in the daemon
**Status:** OPEN
**Phase:** Phase 6 — deferred from Slice A2. Required before any real OneDrive end-user deployment.
**Risk if triggered early:** HIGH for real users, none for the demo. The intended end user runs OneDrive, which offloads rarely-touched files to its cloud and leaves an **online-only placeholder** on disk: the file still appears in the folder, but reading its bytes **forces OneDrive to download (hydrate) it**. This collides with two locked Phase 6 decisions — "content hash = raw file bytes" and "startup + periodic full sweep" (ADR-0013): naive hashing/walking would drag the **entire vault** back down from OneDrive (bandwidth, disk, defeats the offload the user relies on). The demo uses a normal local vault, so it does not trigger; a real OneDrive vault would.
**What:** Make the daemon placeholder-aware. (1) Detect online-only placeholders; **never download a file just to fingerprint it**. (2) Track offloaded files via the daemon cache + cheap metadata (size / modified-time) that OneDrive exposes without triggering a download. (3) Byte-hash **only** files whose content is actually local — a carve-out to the locked "hash = raw bytes" rule. (4) For a never-before-seen offloaded placeholder (daemon installed onto an already-offloaded vault), capture a name/path/size **stub** now so it's findable by name, and capture real content later when the file becomes local — **do NOT force-download the whole vault on install**.
**Why deferred:** Owner decision (2026-06-14 grill) — demo does not need it; real OneDrive deployment does. The "no-hydrate / hash-only-local" principle is ADR-worthy when the capability is built.
**Touches when revisited:** `src/daemon/scanner.py` (sweep), `src/daemon/extractor.py` (hashing), `src/daemon/watcher.py`, daemon cache (A2). Related: ADR-0013, `docs/0_draft/phase6/phase6_A2_grill.md` §2.2.
**Source:** Phase 6 Slice A2 build-pipeline grill, 2026-06-14 (user-raised — end user works with OneDrive, which offloads untouched files to the cloud).

---

### TD-060 · "Why no description" not surfaced to user / consuming AI
**Status:** OPEN
**Phase:** Phase 10 (Web UI) — surfaced during Phase 7B grill (2026-06-14).
**Risk if triggered early:** None — no code yet. Phase 7B stores a blob but leaves its description empty when the file is too big (over the config size cap), the vision call fails, or the file type is unsupported for vision. The *reason* is written to the audit log only. Today nothing reads the audit log back to the user, so the user/consuming AI sees an undescribed image with no explanation of why.
**What:** When a visual/binary capture produces no description, surface the reason (too-big / vision-failed / unsupported-type — read from the audit-log entry Phase 7B writes) to the user and the consuming AI. Per the locked "needs-description is derived, no new flag column" decision (7B decision 9 / 7A decision 6 / ADR-0014), the reason MUST stay in the audit log, not a new `documents` column — the UI reads it from there.
**Why deferred:** No UI exists yet (Phase 10). Phase 7B ships the audit trail (the data); surfacing it is a UI concern. Owner-requested at the 7B grill.
**Source:** Phase 7B build-pipeline grill, 2026-06-14 (user-raised during size-cap discussion).

---

### TD-059 · Dummy vault path workaround in cloud container
**Status:** OPEN
**Phase:** Dies with config split (Phases 6/7/9)
**Risk if triggered early:** None — cosmetic only. Container creates empty `/data/vault/` to satisfy `VaultConfig` validation (`vault.root` must exist on disk). No vault code runs cloud-side. If someone adds vault-dependent code to the container entry point before the config split lands, it would silently operate on an empty directory.
**What:** `core/config.py` `VaultConfig` requires `vault.root` to exist. Cloud container has no vault. Workaround: set `VAULT_ROOT=/data/vault` env var + `mkdir -p /data/vault` in Dockerfile. Ugly but contained.
**Design correction (2026-06-13, user sign-off — design doc OQ-1):** the `VAULT_ROOT` env var has **no binding in `core/config.py`** today (only `KMS_DB_PATH` is overridable). So the workaround additionally requires **adding** a throwaway `VAULT_ROOT` env override to `core/config.py` + a unit test. Chosen over a cloud-specific `config.yaml` to keep one identical image per tester. **Mechanism (research):** NOT a `KMS_DB_PATH` mirror — the override must be injected into the raw config dict *before* `MainConfig` is constructed (the vault-root existence check is a construction-time `MainConfig` validator at `config.py:372-382`; `VaultConfig` has no `validate_assignment`). See spec C2-5.
**Resolution:** When config split happens (Phases 6/7/9 each shed vault dependencies), remove `vault.root` requirement from cloud config entirely. Delete dummy directory from Dockerfile **and remove the `VAULT_ROOT` binding from `core/config.py`**. This TD auto-resolves when the last vault import is removed from cloud-side code.
**Source:** P5 Slice 2 grill (2026-06-13); amended at design (2026-06-13).

---

### TD-058 · End-user dimension/tag creation + infer-and-confirm on new-laptop initialization
**Status:** OPEN
**Phase:** Cross-phase — Phase 6 (daemon first-run init) + Phase 10 (Web UI dimension/tag management). Surfaced during Phase 5 Slice 1 design (2026-06-12).
**Risk if triggered early:** None — design only, no code. Slice 1 ships `config/dimensions.yaml` as a static, technical-team-authored file with a starter taxonomy (people/projects/domains). That is sufficient to build/validate, but it is NOT a product-complete answer for a non-technical end user.
**What:** Two linked gaps the static `dimensions.yaml` leaves open:
  1. **End-user dimension/tag authoring.** A non-technical user must be able to create/rename/remove their OWN dimensions and tag sets without editing YAML by hand. Phase 10's Web UI scope (rearch doc §10) covers *tag* add/remove/replace within an existing dimension — but NOT creating a whole new *dimension*, nor a guided way for a non-coder to do either. Need a function/process (UI flow + a safe write path to the dimension/tag config that re-validates and triggers the house-AI re-scan of `other` entries per rearch doc §7).
  2. **Infer-and-confirm on initialization (new laptop / first run).** When the user sets up on a fresh machine, the system should not start from a generic default taxonomy. It should ask/infer a candidate dimension+tag set (e.g. from an initial vault scan or a short interview) and then CONFIRM it with the user before locking it in. Onboarding decision, not a runtime classify decision.
**Open questions:**
  - Where does inference run — daemon first-run (Phase 6 installer/onboarding) or cloud after the first vault scan?
  - Is the dimension/tag config per-user/per-vault state (so it must persist + back up like the DB), vs the current ships-in-image assumption? (Interacts with the deferred config split and the §11.2 Litestream persistence decision.)
  - How is "confirm with user" surfaced — Web UI wizard, or daemon first-run prompt?
**Why deferred:** Out of Slice 1 scope (Slice 1 is additive data/config foundation only). Needs the Web UI (Phase 10) and the daemon onboarding flow (Phase 6) to exist first. Logged now so the static-YAML starting point is not mistaken for the finished product.
**Source:** Phase 5 Slice 1 build-pipeline design session, 2026-06-12 (user-raised during dimension/tag validation discussion).

---

### TD-050 · Watcher test debounce-timer leak across tests
**Status:** OPEN
**Phase:** Phase 2+ (test infra)
**Risk if triggered early:** Flaky CI — `tests/test_vault/test_watcher_rehome.py::test_no_edit_pdf_cross_folder_rehome` intermittently fails `assert len(move_note_calls) == 1` with `2` when the full suite runs. The extra `move_note` comes from a *different* test's (`test_on_created_misplaced_md…`) leaked `threading.Timer` debounce that fires during this test's `time.sleep(0.05)` and lands on the module-level `vault.watcher.move_note` (now monkeypatched to this test's fake). Passes in isolation. Not caused by P2-CIC.
**What:** Watcher tests schedule real `threading.Timer` debounce callbacks (`_VaultEventHandler._debounce`) that are not cancelled in teardown. A late-firing timer from one test mutates another test's monkeypatched module-level functions. Fix needs: (1) identify the producing test(s), (2) cancel pending timers in teardown — e.g. an autouse fixture in `tests/test_vault/conftest.py` that tracks/cancels handler timers, or a `handler.stop()`/`shutdown` that joins outstanding timers.
**Why deferred:** Threading-sensitive, cross-file; not a small safe change. Pre-existing before P2-CIC review.
**Source:** P2-CIC implementation review 2026-06-08 (reviewer Minor + main-thread investigation).

---

### TD-051 · classify() destination validation pools project + domain names (cross-type leak)
**Status:** RESOLVED (P3 Session B, commit c011fed, 2026-06-11)
**Phase:** ✅ Resolved
**Risk if triggered early:** N/A
**What:** Split validation into project-name vs domain-name sets. RESOLVED: `_build_vault_context()` now returns `(valid_destinations, project_names, domain_names)` from the structured `ProjectRegistry` (group names = domains, entry names = projects); `classify()` validates `project ∈ project_names` and `primary_domain ∈ domain_names`, falling back to the legacy pooled `_destination_names()` path only when the registry is unavailable (signalled by `None, None`). Test fixtures rewritten to the real `format_for_prompt` shape; the classify prompt was updated (P3 review finding I-4, 2026-06-11) to teach the model the domain-header vs project-item distinction the code now enforces.
**Why deferred (historical):** Needed a small signature/fixture rework; the substring defect was already fixed before this split.
**Source:** P2-CIC implementation review 2026-06-08 (finding #2 residual); resolved by P3 Session B + P3 implementation review 2026-06-11.

---

### TD-053 · Filter-only global search scans the catalog row-by-row (O(N))
**Status:** OPEN (monitor)
**Phase:** Phase 3+ (fix when vault grows)
**Risk if triggered early:** Low now. `retrieval/search.py::_search_filter_only` resolves the global path via `all_paths()` then calls `get_by_path()` once per note (N single-row queries) before capping at `limit`. Fine for a small vault; cost grows linearly with catalog size. Only the filter-only/global path (bare `kms search`, or `--project`/`--since` with no query) is affected — the query path is already bounded by `max_candidates`.
**What:** Replace the N×`get_by_path` loop with a single `SELECT ... ORDER BY updated_at DESC LIMIT ?` (with optional `WHERE vault_path IN (...)` for the scoped case) in the data layer, returning hydrated rows directly.
**Why deferred:** Not a correctness bug; vault is small pre-launch. User decision (2026-06-11 P3 review): monitor, fix when it becomes a real latency issue.
**Source:** P3 implementation review 2026-06-11 (reviewer B, Minor).

---

### TD-056 · `kms_write` MCP tool + field-level metadata guard in capture pipeline
**Status:** ⚠️ STALE under cloud-native rearchitecture (2026-06-12) — the premise (AI writes `.md` to the vault; field-level frontmatter guard) is DEAD: the system never writes to the vault, frontmatter retires, `updated_by_human` retires (rearch doc §5/§6/§12, ADR-0012). If AI-authored knowledge is still wanted, it belongs in the DB (`knowledge_entries`/`documents`), not vault files — re-scope as a NEW item against the rearchitecture before building. Do NOT implement as written.
**Phase:** ~~Phase 4~~ (superseded — see above)
**Risk if triggered early:** None — design only; no code yet
**What:** AI in chat needs to write notes to the vault with user-directed metadata (tags, project, etc.) that survives capture pipeline re-processing. Two linked problems:
  1. **`kms_write` tool:** AI creates a `.md` note in inbox (or target folder) with frontmatter reflecting user intent (e.g., "save this as a Movies note with tag strategy"). Watcher detects → capture pipeline runs. Currently, pipeline overwrites ALL frontmatter — user intent lost.
  2. **Field-level metadata guard:** When capture re-processes a note (content hash changed), it should distinguish "fields to regenerate" (summary, key_topics — derived from body) from "fields to preserve" (tags, project — set by user or AI on user's behalf). Guard must be field-level, not note-level, because body edits should still trigger fresh summary generation.
  **Design decisions reached:**
  - `kms_write` replaces the original `kms_capture` MCP tool concept. AI writes note, watcher/pipeline processes it.
  - NOT using `updated_by_human` as the guard — that flag is not durable and will be removed.
  - Need a new mechanism to mark field-level ownership (e.g., `_locked_fields` list in frontmatter, or a hash-per-field approach, or a `set_by` provenance stamp). Design TBD.
  - This also fixes the existing bug: user edits note body → hash changes → pipeline re-runs → user's manually-set tags/project get overwritten.
  **Open questions:**
  - What mechanism marks a field as "user-owned" vs "pipeline-owned"?
  - Should `kms_write` write directly to target folder (skipping classify) or always to inbox?
  - How does `kms_move` (see TD-057) interact with field-level guard?
**Key user scenario blocked:** User says "capture this discussion for me" or AI proactively wants to save a learning/insight from conversation → needs to write a note to vault. Without `kms_write`, AI has no way to create notes via MCP. This is a core use case, not a nice-to-have.
**Why deferred:** Deep capture pipeline change (field-level guard). Phase 4 MCP can ship with read-path tools + `kms_move` first; `kms_write` follows once guard design is resolved.
**Source:** Context injection design grilling session 2026-06-11

---

### TD-057 · `kms_move` MCP tool for AI-directed note relocation
**Status:** ⚠️ STALE / DEAD under cloud-native rearchitecture (2026-06-12) — the system never moves or reorganizes user files (rearch doc §6, ADR-0012); `kms_move` + `_move.py` are explicitly retired in Phase 9. `move_guard`, the CLUELESS-move concept, and folder routing are all dead. Do NOT implement. (Note: `kms_move` WAS shipped in Phase 4 for the local-only model; it is removed by the rearchitecture, not built anew.)
**Phase:** ~~Phase 4~~ (retired by rearchitecture Phase 9)
**Risk if triggered early:** None — design only; no code yet
**What:** When user asks AI to "classify my inbox," AI should read CLUELESS notes (which have `classify_reasoning` and `classify_confidence` in frontmatter explaining why classification failed), present the reasoning to the user, ask for guidance, then move the note to the user-specified folder. This requires a `kms_move` MCP tool — thin wrapper around `move_note()` + `documents.replace_path()`. AI does NOT re-invoke `kms_classify` (same input = same CLUELESS result); instead AI acts on human judgment and moves directly.
  **Design decisions reached:**
  - `kms_move` replaces the original `kms_classify` MCP tool concept for the conversation use case.
  - CLUELESS notes already have `classify_reasoning` stamped in frontmatter (verified in capture.py) — AI can read and present this to user.
  - `kms_move` should update frontmatter `project`/`primary_domain` fields to match destination folder.
  - `kms_move` should use `move_guard` to prevent watcher from re-homing the note.
**Why deferred:** Ship alongside other MCP tools in Phase 4. Not blocked by TD-056 — move is a physical operation, not a metadata one.
**Source:** Context injection design grilling session 2026-06-11

---

### TD-054 · Auto-generate and maintain `CLAUDE.md` and `context.yaml` for Domain and Project folders
**Status:** ⚠️ SUPERSEDED by cloud-native rearchitecture (2026-06-12) — per-project/domain `CLAUDE.md` + `context.yaml` files are replaced by the `knowledge_entries` DB table (rearch doc §3/§7). The INTENT survives (auto-extract structured project/people/domain knowledge over time) but the mechanism is now Phase 8 classify writing `knowledge_entries`, NOT vault files. Track the intent under Phase 8; do not build the file-based version.
**Phase:** ~~Post-Phase 4~~ (intent moves to rearchitecture Phase 8)
**Risk if triggered early:** None — MCP context injection gracefully falls back: missing `context.yaml` → inject only `CLAUDE.md`; missing `CLAUDE.md` → inject only `context.yaml`; both missing → no context injected for that domain/project (search results still returned normally)
**What:** Two context files per Domain/Project currently require manual authorship:
  - `CLAUDE.md` — project/domain instructions, background, current status, stakeholders
  - `context.yaml` — structured domain knowledge: people, metrics, vocabulary/jargon (Domain folders only)
The system should auto-generate and maintain both files by extracting knowledge from captured notes over time. This is invisible backend magic: the user does nothing, but the AI understands their projects, stakeholders, KPIs, and workplace vocabulary. Implementation: a pipeline stage that periodically scans domain/project notes, extracts relevant knowledge, and upserts these files (respecting `updated_by_human` if the user has hand-edited them).
**Why deferred:** Phase 4 (MCP) can read existing files as-is and degrade gracefully when they're absent; auto-generation is an enhancement that improves context quality over time but is not blocking.
**Source:** Context injection design grilling session 2026-06-11

---

### TD-055 · Build AI-facing skills/instructions for correct MCP vault usage
**Status:** OPEN
**Phase:** Phase 4 (ship alongside MCP server; AI needs guidance to use tools correctly)
**Risk if triggered early:** None — instructions are documentation, not code
**What:** The MCP tools need accompanying AI instructions (via tool descriptions, user personal preferences, or a dedicated skill/system prompt) so the AI knows HOW to use the vault correctly. Distilled from the context injection design session, the AI must be taught:
  **Discovery & orientation:**
  1. **Start with `kms_vault_info`:** Call this first in any new session. Returns available projects, domains, inbox count, last capture time, and vault-root CLAUDE.md (global user context — who they are, what they manage). Use exact project/domain names from this response when passing `project` parameter to `kms_search`.
  2. **Never assume vault structure:** Always discover via `kms_vault_info` or `kms_search`. Don't hardcode paths or guess folder names.
  **Search & read flow:**
  3. **Two-step retrieval flow:** Call `kms_search` first (get context + cards), then `kms_read` for full content. Never skip search and go straight to read blindly.
  4. **Context-before-content ordering:** Read the CLAUDE.md/context.yaml blocks BEFORE reading result cards or full note content. These provide background needed to interpret results correctly.
  5. **Hash-deduped context:** Context files include content hashes. Server handles dedup automatically — already-sent context is replaced with a short "context for X already provided" note. AI does not need to track this.
  6. **Batch reads:** When reading multiple notes, pass all paths in a single `kms_read` call (list of paths) to minimize round trips.
  7. **Use `kms_inspect` for binary source material:** When a search result represents a binary file (type=attachment-summary), `kms_read` returns the AI-generated summary. To get the full extracted text from the original binary, use `kms_inspect`. Accepts either the sibling `.md` path or the binary path.
  **Query strategy:**
  8. **Use structured filters when confident:** Pass `project`, `date_range`, and `location` parameters to `kms_search` when you can extract them from user intent. `project` filters by metadata (semantic), `location` filters by vault folder (physical, e.g., `"inbox"`). Fall back to free-text-only when unsure.
  9. **Query refinement is expected:** After reading stage-1 context + cards, you may decide to refine the query or search again with different terms. This is normal.
  10. **Broad queries skip context:** Broad queries (e.g., "what happened this week") may return zero context files — this is correct. Only domain/project-concentrated results trigger context injection.
  **Write-path tools (pending TD-056, TD-057):**
  11. **`kms_write` for new content:** Use to create notes on behalf of the user — capturing discussions, saving learnings/insights from conversation, or any content the user wants preserved. AI sets frontmatter (tags, project) reflecting user intent; capture pipeline processes but preserves user-set fields. Also use proactively when a conversation produces knowledge worth keeping. (Not yet implemented — see TD-056.)
  12. **`kms_move` for CLUELESS resolution:** When user asks to classify inbox, read CLUELESS notes' `classify_reasoning` frontmatter, present the reasoning to user, ask for guidance, then move with `kms_move`. Do NOT re-invoke classify on a CLUELESS note. (Not yet implemented — see TD-057.)
  13. **`include_context=true` escape hatch:** If you feel you're missing background context mid-conversation (e.g., after session state reset), force context re-injection on any search/read call.
**Delivery format:** TBD — could be MCP tool descriptions, a skill file, user personal preferences block, or a combination. Decide during Phase 4 implementation.
**Why deferred:** Instructions depend on final MCP tool API design, which is still being grilled.
**Source:** Context injection design grilling session 2026-06-11

---

### TD-039 · Windows support for binary content-change detection + atomic-save handling
**Status:** OPEN
**Phase:** Vault-restructure (post-Mac-ship)
**Risk if triggered early:** Designing Windows handling before the macOS change-detection layer exists wastes effort; the abstraction must settle on Mac first.
**What:** Content-change detection (draft Task T9) is Mac-first by decision. Windows needs its own work before any Windows user: (1) a real-vault event probe for Windows Office atomic-save sequences (differs from macOS — `~WRDxxxx.tmp` rename dance), (2) a read-when-unlocked retry path (Windows holds an exclusive lock on open Office files, so the file can't be hashed until released), (3) Windows temp-file ignore patterns (`~WRD*.tmp`, `~$*`). `watchdog` unifies the API but NOT the event sequences or locking semantics.
**Why deferred:** Target user confirmed Mac-first for the June demo. No Windows user until later.
**Source:** Grilling session 2026-06-03; `docs/draft/vault-restructure-editable-noedit-split.md` (T9, TD-W1 placeholder).

---

### TD-004 · embeddings table + FTS5 virtual table
**Status:** OPEN
**Phase:** Phase 3
**Risk if triggered early:** No consumer for embeddings exists until retrieval layer; premature schema addition adds migration complexity with no return.
**What:** `embeddings` table and FTS5 virtual table are not yet created; retrieval layer in Phase 3 will require both.
**Why deferred:** No consumer until retrieval layer exists.
**Source:** Out of Scope, `plans/storage_level.md`

---

### TD-005 · corrections table enrichment with classifier-specific fields
**Status:** OPEN
**Phase:** Phase 7
**Risk if triggered early:** Self-learning schema requires classifier design to be finalized; adding fields before Phase 7 design creates schema churn.
**What:** Placeholder `corrections` table exists; fields for self-learning classifier (feedback type, correction delta, confidence) not yet added.
**Why deferred:** Fields added when self-learning is built in Phase 7.
**Source:** Out of Scope, `plans/storage_level.md`

---

### TD-006 · Per-section AI vs human authorship tracking
**Status:** OPEN
**Phase:** Phase 7+
**Risk if triggered early:** Hard problem; extending `updated_by_human` or adding `edits` table without the full design risks schema lock-in.
**What:** `updated_by_human` is a whole-note boolean blunt gate. Per-section authorship (e.g. "AI wrote this summary, human wrote this conclusion") requires a separate design — HTML comments or an `edits` table.
**Why deferred:** Fine-grained tracking is explicitly out of scope until Phase 7+. See Open Question Q-002.
**Source:** Open Question Q-002, `plans/storage_level.md`

---

### TD-007 · Daemon-mode WAL checkpoint tuning (wal_autocheckpoint)
**Status:** OPEN
**Phase:** Phase 4
**Risk if triggered early:** WAL checkpoint tuning only matters in long-running daemon; CLI closes cleanly and WAL truncates on exit.
**What:** Reference project sets `wal_autocheckpoint=100`; SQLite default is 1000 pages. Worth revisiting before Phase 4 MCP daemon.
**Why deferred:** CLI exits cleanly; WAL truncates on close. Irrelevant until MCP daemon.
**Source:** Out of Scope + Open Question Q-003, `plans/storage_level.md`

---

### TD-008 · documents columns: project, status, key_topics
**Status:** RESOLVED (Phase Pre-2, 2026-06-03)
**Phase:** Phase 2+
**Resolution:** 3 SQL migrations added (003_add_project, 004_add_status, 005_add_key_topics). DocumentRow updated with all three fields. `_row_from_sqlite`, `upsert`, `replace_path` updated. 797 tests pass.
**What:** `documents` table lacks `project`, `status`, `key_topics` columns. These will be added via migrations when classify/search pipelines need them.
**Why deferred:** Add via migrations when pipelines demand them; not pre-emptively.
**Source:** Out of Scope, `plans/storage_level.md`

---

### TD-009 · updated_by_human sync between frontmatter and SQLite
**Status:** OPEN
**Phase:** Phase 1 (post-delivery audit)
**Risk if triggered early:** Sync logic depends on watcher being able to detect human edits reliably; premature sync without watcher risks false-positive locks.
**What:** `updated_by_human` exists in both frontmatter and `documents` table. The sync logic (frontmatter → SQLite on human edit detection) lives in `vault/writer.py` and watcher; edge cases around simultaneous human+AI writes are not fully exercised.
**Why deferred:** SQLite mirror exists for cheap queries; sync logic is a `vault/writer.py` concern deferred for post-capture audit.
**Source:** `research/storage_level.md` edge cases

---

### TD-010 · Ollama httpx async rewrite
**Status:** OPEN
**Phase:** Phase 3+
**Risk if triggered early:** `asyncio.to_thread(requests.post)` is sufficient for Phase 0/1/2; rewriting before Ollama becomes a performance bottleneck is speculative work.
**What:** `OllamaProvider` uses `asyncio.to_thread(requests.post)` — a sync HTTP call wrapped in a thread. A native `httpx` async rewrite would eliminate thread overhead.
**Why deferred:** Only worth revisiting if Ollama becomes performance-critical in Phase 3+.
**Source:** Out of Scope, `plans/llm_layer.md`

---

### TD-011 · Per-prompt model and temperature overrides
**Status:** OPEN
**Phase:** Phase 1+
**Risk if triggered early:** Extending `LLMProvider.complete()` signature requires updating all three providers and all call sites simultaneously.
**What:** `Prompt` dataclass has no `model` or `temperature` fields; removed as dead weight in Phase 0. Per-prompt overrides require extending `LLMProvider.complete()` signature.
**Why deferred:** No caller needs per-prompt overrides yet; extend signature when needed.
**Source:** DECISION-016 + review finding #3

---

### TD-012 · cli/main.py stubs: classify, search, briefing commands
**Status:** OPEN
**Phase:** Phase 2+
**Risk if triggered early:** Stubs with no backing pipeline are lies; add commands only when pipeline exists and is tested (C-15).
**What:** `capture`, `capture --scan`, and `watch` delivered in Phase 1. `classify`, `search`, and `briefing` CLI commands are still stubs.
**Why deferred:** Backing pipelines not yet built.
**Source:** `cli/main.py` created as dotenv owner (DECISION-014)

---

### TD-013 · embedding_model field stored but not yet routed
**Status:** OPEN
**Phase:** Phase 3
**Risk if triggered early:** `sentence-transformers` wiring belongs in Phase 3 retrieval; adding it earlier creates unused infrastructure.
**What:** `_embedding_model` field exists on all three provider configs for single-provider portability. Phase 3 retrieval will wire it to `sentence-transformers` or provider embedding endpoint.
**Why deferred:** No retrieval consumer yet.
**Source:** DECISION-015; Out of Scope, `plans/llm_layer.md`

---

### TD-015 · CLAUDE.md section-merge for AI co-authoring
**Status:** OPEN
**Phase:** Phase 12+
**Risk if triggered early:** Section-merge requires understanding wikilink + section structure; implementing before Phase 11 watcher makes the merge logic untestable.
**What:** Watcher delivered (Phase 11, 2026-05-21). Section-merge for `CLAUDE.md` AI co-authoring is still deferred. Interim rule: AI writes `CLAUDE.md` with `actor="ai"`; `updated_by_human` stays `False`; human context edits can be overwritten by AI index writes until section-merge lands.
**Why deferred:** Hard merge problem; watcher is prerequisite.
**Source:** `plans/vault_layer.md` OQ-V8; review session 2026-05-18

---

### TD-016 · User explicit URL flagging in enrich_urls
**Status:** OPEN
**Phase:** Phase 1+ (post-watcher)
**Risk if triggered early:** Requires extending `RawContent` or passing note metadata separately to `enrich_urls`; premature extension before the rest of enrich_urls stabilizes risks rework.
**What:** User marks URLs as crucial via frontmatter `fetch_urls: [url1, url2]` or inline `#fetch` tag. These bypass the structural gate and are always fetched. Implementation: `enrich_urls` reads `fetch_urls` from `RawContent` frontmatter and merges with gate-selected URLs. Isolated in `_build_gate` extension point.
**Why deferred:** Wishlist item; no user demand yet.
**Source:** `docs/research/capture_pipeline.md` Wishlist A

---

### TD-017 · AI URL triage replacing structural heuristic gate
**Status:** OPEN
**Phase:** Phase 2+
**Risk if triggered early:** Adds LLM latency to every capture with URLs; requires new prompt YAML and test coverage before deploy.
**What:** Before fetching, LLM classifies each URL as `primary | citation | skip` using `prompts/url_triage.yaml`. Only `primary` URLs are fetched. Replaces `_should_enrich` structural heuristic with `_ai_triage_urls()` inside `_build_gate`.
**Why deferred:** Adds latency; structural heuristic sufficient for Phase 1.
**Source:** `docs/research/capture_pipeline.md` Wishlist B

---

### TD-018 · Domain list refresh in kms watch
**Status:** OPEN
**Phase:** Post-Phase 11
**Risk if triggered early:** Dynamic refresh requires a file-system watcher on `Domain/` itself, adding complexity to the watcher startup sequence.
**What:** Taxonomy loaded once at watcher startup; new `Domain/` folders added while watcher runs are invisible until restart.
**Why deferred:** Acceptable for Phase 11; dynamic refresh deferred.
**Source:** OQ-C6, `plans/capture_pipeline.md`

---

### TD-019 · Tag taxonomy enforcement in classify pipeline
**Status:** OPEN
**Phase:** Phase 2 (Roadmap)
**Risk if triggered early:** `validate_tags` is shared infrastructure; wiring it into classify before the pipeline exists creates a stub dependency.
**What:** `core/tags.py::validate_tags` is wired in the capture pipeline. Phase 2 classify pipeline must wire it in too.
**Why deferred:** Not in capture plan scope; classify is Phase 2.
**Source:** Out of Scope, `plans/capture_pipeline.md`

---

### TD-020 · docs/research/capture_pipeline.md §"Non-md branch" documents OLD attachment layout
**Status:** OPEN
**Phase:** Brief #2 post-ship
**Risk if triggered early:** Annotating docs before Brief #2 ships risks annotating against a design that still changes.
**What:** `docs/research/capture_pipeline.md` §"Non-md branch" describes the old layout: sibling next to source, global `attachment_path`, bare wikilink. Superseded by `revise_attachment_layout.md`. Needs annotation: "→ see revise_attachment_layout.md for new layout".
**Why deferred:** Deferred until Brief #2 shipped the new behavior.
**Source:** `docs/research/revise_attachment_layout.md` TD-RAL-1

---

### TD-021 · docs/roadmap.md Phase 1 describes OLD attachment layout
**Status:** OPEN
**Phase:** Brief #2 post-ship
**Risk if triggered early:** Same as TD-020 — annotating before Brief #2 ships risks rework.
**What:** `docs/roadmap.md` Phase 1 (lines 53–66) describes global `Vault/attachment/` layout in detail. Needs annotation or rewrite.
**Why deferred:** Documentation pass deferred until Brief #2 shipped.
**Source:** `docs/research/revise_attachment_layout.md` TD-RAL-2

---

### TD-029 · Rename gate logic mis-calibrated
**Status:** OPEN
**Phase:** Phase 2 pre-req
**Risk if triggered early:** Auto-rename without confidence scoring risks renaming files the user intentionally named; fix requires research first.
**What:** `_should_rename_md` fires when title looks generic but skips when title is descriptive-yet-stale. Needs survey of how other KMS tools (Logseq, Reflect, mem.ai) handle filename vs heading drift. Likely fix: confidence-scored rename suggestion + human-review queue rather than auto-rename.
**Why deferred:** Requires research into competitor approaches before calibration.
**Source:** STATE.md "Re-make work" §3

---

### TD-031 · move_attachment TOCTOU window
**Status:** OPEN
**Phase:** Watcher hardening pass
**Risk if triggered early:** Only a real risk in concurrent watcher mode; single-process CLI is safe. Fix requires testing EXDEV cross-filesystem fallback for `os.link`.
**What:** `vault/writer.py::move_attachment` has a TOCTOU window between `dst.exists()` check and `os.replace(src, dst)`. Fix: replace with `os.link(src, dst)` (atomic, raises `FileExistsError` if dst exists) + `src.unlink()`. Keep EXDEV cross-filesystem fallback.
**Why deferred:** Acceptable in single-process CLI; risk is in watcher mode with concurrent drops + scan.
**Source:** Code review 2026-05-24 (issue #8)

---

### TD-032 · No kms migrate-attachments command for legacy vault layout
**Status:** OPEN
**Phase:** Post-Phase 2 (only if needed)
**Risk if triggered early:** No production users with legacy layout; building migration before anyone needs it is waste.
**What:** No migration path from pre-Brief-#1 layout (global `Vault/attachment/` + `Vault/Archive/`) to current per-project/per-domain layout. If/when needed: `kms migrate-attachments` one-shot that walks legacy folders, infers project/domain (via frontmatter or prompt), moves binary + sibling, updates DB `vault_path` rows.
**Why deferred:** Greenfield — no production users with legacy layout.
**Source:** Code review 2026-05-24 (issue #9)

---

### TD-033 · vault.watcher monkeypatching target documentation
**Status:** OPEN
**Phase:** Documentation-only
**Risk if triggered early:** N/A — documentation-only entry. No code change needed; the risk is re-introducing the bug in future tests.
**What:** `vault/watcher.py` hoists all collaborators as top-level imports. Tests MUST patch `vault.watcher.<name>` (e.g. `vault.watcher.move_note`), NOT the source module (`vault.writer.move_note`). Patching the source module leaves `vault.watcher`'s local binding pointing at the original. All existing watcher tests updated 2026-05-24.
**Why deferred:** Documentation-only; code is already correct. Tracked to prevent future test authors from re-introducing the bug.
**Source:** Code review 2026-05-24 (issue #10/#11 follow-on)

---

### TD-035 · Reconcile: location-tag mismatch when human has overridden domain/project
**Status:** OPEN
**Phase:** Phase 1.5 reconcile (deferred)
**Risk if triggered early:** Silent inconsistency. A file can sit in `Projects/Alpha/` with `project: Beta` set by human and the system will never surface the conflict.
**What:** When location-confidence tagging runs, if `updated_by_human: true` guards a `project:` or `domain/X` tag, the pipeline skips the override (human wins). But the mismatch — file location contradicts human-set tags — is never surfaced. The reconcile pipeline should detect this case and either alert the user or offer to override.
**Why deferred:** Reconcile alert/override logic is a separate concern from the capture-time tagging decision. Build capture behavior first, then extend reconcile.
**Unblock condition:** Extend reconcile pipeline to compare file location against `project:` / domain tags when `updated_by_human: true`. Emit a mismatch audit entry or user-visible alert.
**Source:** Phase 1.5 domain/project tagging design session 2026-06-01

---

### TD-036 · Reconcile: stale batch_id on documents after file moves
**Status:** OPEN
**Phase:** Phase 1.5 (folder handling) / post-MVP
**Risk if triggered early:** Low. `batch_id` is observability metadata; stale refs don't affect routing. Phase 8 Briefing would group a moved file under its original batch, which is misleading but not harmful.
**What:** When a file captured as part of a folder batch is later moved out of the batch destination (e.g. from `Projects/A/` to `Projects/B/`), the `documents.batch_id` FK is preserved by `rename_doc` (which only updates `vault_path`). The file now has a stale batch association pointing to its original folder group. Add a reconcile stage (`reconcile_stale_batch_refs`) that: (1) JOINs `documents` with `batches` on `batch_id`, (2) checks if `documents.vault_path` still falls under `batches.destination_type`/`destination_name` prefix, (3) nulls out `batch_id` on rows where location no longer matches, (4) optionally sets `batches.status = STALE` when all member docs have drifted.
**Why deferred:** `batches` table and `batch_id` column not yet implemented. Add this stage when folder-handling spec ships. Eventual consistency (stale for minutes between reconcile runs) is acceptable for `batch_id`.
**Unblock condition:** `batches` table and `documents.batch_id` column exist. Then add Stage 6 to `reconcile()` in `pipelines/reconcile.py` and add `batch_refs_cleared: int = 0` to `ReconcileResult`.
**Source:** Phase 1.5 folder handling design session 2026-06-02

---

### TD-034 · Project-to-domain mapping registry
**Status:** OPEN
**Phase:** Phase 1.5 / deferred
**Risk if triggered early:** No domain→project mapping exists in code. Active projects live flat under `Projects/<A>/` with no domain in path. Any feature that needs "which domain does project A belong to" will silently fall back to `Uncategorized` until this is resolved.
**What:** When a file lands under `Projects/<A>/`, the capture pipeline needs to set a `domain/<D>` tag. Currently no registry stores which domain an active project belongs to. Interim rule: domain tag is set to `domain/Uncategorized` for project-scoped files.
**Why deferred:** Two clean options exist (a `projects` DB table or `Projects/<A>/meta.yaml`), but picking one requires design work. `Projects/<A>/CLAUDE.md` was rejected — it's prose, not structured, and TD-015 means AI can overwrite human edits there.
**Unblock condition:** Design and implement a project registry (DB table preferred) that stores `project_name → domain_name`. Update location-confidence tagging to do a registry lookup instead of defaulting to `Uncategorized`.
**Source:** Phase 1.5 domain/project tagging design session 2026-06-01

---

### TD-037 · Binary modify never re-captures (formalizes the `TD-C6` code marker)
**Status:** RESOLVED (Vault-Restructure Phase 8/9, 2026-06-04)
**Resolution:** Binary content-change detection implemented — watcher compares SHA-256 on modify events, lock-file filter handles Office atomic saves, settle window coalesces multi-hop moves. 143 new tests cover the behavior.
**Phase:** Watcher hardening pass (own phase, after Phase 1.5 fix batch)
**Risk if triggered early:** Stale knowledge. Office files (`.xlsx`, `.docx`, `.pptx`) get edited frequently; their summary siblings under `attachment/.summaries/` never regenerate, so briefings surface outdated content with no signal that it drifted.
**What:** A binary modified in `Projects/<A>/attachment/` or `Domain/<D>/attachment/` never re-runs capture. `vault/watcher.py::on_modified` (line 229-238) double-blocks it: (1) `_should_skip(path)` drops managed-attachment binaries first, then (2) an explicit `if suffix != ".md": return` guard (the `# Binary modify deferred — TD-C6` comment). The downstream machinery already works — `_store_nonmd` LOCATED path recomputes `source_hash` + overwrites the sibling, and Phase 6 idempotency means unchanged content → `SKIPPED`, changed content → re-run. Only the watcher trigger is missing.
**Why deferred:** Net-new behavior (binary modify never worked), not a regression from the pay_debt plan. Lives in the watcher, which is concurrency-sensitive and already receiving the C2 timer-race fix — stacking a second event-path change raises risk. Needs its own test surface: Office saves emit a *burst* of modify events (debounce must coalesce), re-capture idempotency, and no modify→write→modify loop.
**Unblock condition:** Reorder `on_modified` so binary handling runs before `_should_skip` (mirror the `on_deleted`/`on_moved` pattern from Brief #3), add `_handle_binary_modify` → debounce → `capture_file(binary)`. Sibling path is deterministic via `_sibling_for` — no real "reverse lookup" needed. Add coalescing + loop-safety tests.
**Source:** Code review 2026-06-03 (M3); formalizes the `TD-C6` marker at `vault/watcher.py:236`

---

### TD-038 · Drop redundant scalar `domain:` frontmatter field
**Status:** OPEN
**Phase:** Phase 2 (Classify) pre-req or dedicated cleanup phase
**Risk if triggered early:** Drift. Domain is stored in two places — the scalar `domain:` property (`NoteMetadata.domain`, written by `store()` from `mr.ai_domain`) and the `domain/<D>` tag in the unified `tags:` list (written by `apply_location_tags`). `apply_location_tags` syncs both at capture time, but `reconcile_stale_tags` (Stage 5) only touches `tags` + `project` — it does NOT re-sync the scalar `domain:`. So after a domain folder is renamed/removed, the tag can be corrected while the scalar silently keeps a stale value.
**What:** Per Obsidian convention there is one canonical tag field (`tags:`). The scalar `domain:` field is redundant with the `domain/<D>` tag. Decision (user, 2026-06-03): drop the scalar `domain:` entirely; domain lives only as a `domain/<D>` tag in `tags:`.
**Why deferred:** Multi-file refactor: remove `domain` from `NoteMetadata` (`frontmatter.py`) + `_KNOWN_KEYS` + `field_validator`; remove the `domain=mr.ai_domain` kwarg from `store()`; add `_DEPRECATED_KEYS` filter in `dumps()` for lazy migration of existing notes; migration/cleanup pass for existing notes carrying `domain:`. Out of scope for the Phase 1.5 fix batch.

Note: `MetadataResult.ai_domain` is **kept** as internal pipeline state — `apply_location_tags` uses it to append the `domain/<D>` tag. Only the frontmatter scalar `NoteMetadata.domain` is dropped. (Resolved in design: `docs/design/phase_pre_2/td_038_drop_domain_scalar.md`)
**Unblock condition:** Confirm no consumer (Phase 2 Classify, search, briefing) depends on the scalar `domain:`; then remove the field, update the metadata stage to emit only the `domain/<D>` tag, and add a one-shot pass to strip `domain:` from existing frontmatter.
**Source:** Code review 2026-06-03 (I3); user decision to override AI domain via location tag

---

### TD-040 · Single-file capture never sets batch_id — batch association only possible via folder drop
**Status:** RESOLVED (P2-BAT, 2026-06-09)
**Resolution:** `is_batch_subfolder()` predicate added to `vault/paths.py`; batch-stamp pre-step added to `capture_file()` at line 1384 — looks up or creates a batch row for any file dropped into a batch-worthy subfolder. `update_batch_id()` added to `storage/documents.py`. SQL migration 006 adds `folder_path` column to `batches`. All single-file paths (CLI, scan, watcher) now set `batch_id` when the drop target is a recognized subfolder.
**Phase:** Phase 2 / Phase 8 (Briefing)
**Risk if triggered early:** Phase 8 Briefing groups content by batch. Any file captured individually — via `kms capture <file>`, `kms capture --scan`, or watcher single-file drop — will never appear in a batch grouping in the briefing, even if the user intentionally dropped several files into the same project folder one by one. The batch signal exists only for folder drops; individual drops are invisible to the batch layer.
**What:** `capture_file()` is called with `ctx.batch_id = None` in all single-file code paths (CLI, scan, watcher on_create). `_insert_batch()` is only called inside `capture_folder()`, which is only triggered when the watcher detects a stable folder drop. There is no mechanism for single-file capture to associate itself with a batch — not by destination folder, not by time window, not by user intent.
**Why deferred:** Folder-batch capture was the MVP design. Single-file batch association requires a new mechanism: either a time-window coalescer (files landing in the same destination within N seconds share a batch) or an explicit user signal. Neither was designed for Phase 1.
**Unblock condition:** Design a batch-association strategy for single-file drops. 
**Source:** P15-REC-05 test setup 2026-06-07 — test revealed no CLI/watcher path sets batch_id for individual files.

---

### TD-041 · scan_capture does not classify or batch-capture inbox subfolders
**Status:** RESOLVED (P2-BAT, 2026-06-09)
**Resolution:** `scan_capture()` now includes a subfolder-detection pass (lines 1474–1504 in `capture.py`): unprocessed batch-worthy subfolders in `inbox/`, `Projects/`, and `Domain/` are dispatched via `capture_folder()`. Already-located folders skip the LLM classify step (Case B dedup). Mirrors the watcher-triggered folder-drop path.
**Phase:** Phase 2 / Phase 8 (Briefing)
**Risk if triggered early:** P15-FOLD-01 describes folder classification as a `kms capture --scan` capability. Current code: `scan_capture()` calls `capture_file()` per individual file and never calls `capture_folder()`. A folder sitting in `inbox/` is walked file-by-file with no LLM classification, no batch row, and no batch_id on the resulting documents. Files inside `inbox/<subfolder>/` and `Projects/<A>/<subfolder>/` and `Domain/<D>/<subfolder>/` are all captured individually without grouping.
**What:** `scan_capture()` (`src/pipelines/capture.py`) does not detect or dispatch subfolder drops. `capture_folder()` is triggered only by `vault/watcher.py` on a stable folder-creation event, not by any CLI scan path. The desired behavior: `kms capture --scan` should detect unprocessed subfolders in `inbox/`, classify them via LLM, create a batch row, and assign a `batch_id` to all files inside — mirroring what `capture_folder()` does for watcher-triggered drops.
**Why deferred:** Watcher-triggered folder capture was the Phase 1.5 MVP. Extending `scan_capture` to detect and classify subfolders requires: (1) a subfolder-detection pass in `scan_capture`, (2) deduplication logic so a folder already processed by the watcher is not re-classified, (3) batch-id assignment for files inside `Projects/<A>/<subfolder>/` and `Domain/<D>/<subfolder>/` without re-routing (already located).
**Unblock condition:** Design the subfolder-detection pass. 
**Source:** P15-FOLD-01 behavior review 2026-06-07 — test trigger and expected outcome describe scan capability that does not exist in `scan_capture()`.

---

### TD-042 · reconcile_stale_tags (Stage 5) does not mark dirty for deprecated frontmatter keys
**Status:** CLOSED (2026-06-07 — implemented in `src/pipelines/reconcile.py` Stage 5; 3 tests P2-REC-01/02/03)
**Phase:** Phase Pre-2 / Phase 2
**Risk if triggered early:** PRE2-DOM-01 test passes setup but reconcile silently no-ops — `domain:` scalar stays on disk indefinitely for notes with no other dirty condition. Lazy migration never fires unless some other pipeline stage writes the file for an unrelated reason.
**What:** `reconcile_stale_tags` (`src/pipelines/reconcile.py`, Stage 5) sets `dirty=True` only for stale/missing domain tags and wrong `project:` field. It does not check `note.metadata.extra` for keys in `_DEPRECATED_KEYS`. A note that has `domain: finance` in frontmatter but already has a valid `domain/Finance` tag and correct `project:` field will never be written by Stage 5 — so `dumps()` never gets to strip the deprecated key. Fix: add `if any(k in note.metadata.extra for k in _DEPRECATED_KEYS): dirty = True` to Stage 5 before the `if not dirty: continue` guard (line ~359 in reconcile.py). The `model_copy` call at line 362 already preserves `extra`, so `dumps()` will strip it on the next write automatically.
**Why deferred:** Stage 5 was designed to fix tag/project mismatches. Deprecated-key stripping was assumed to be covered by any write — but notes with no other dirty condition are never written by Stage 5, leaving the key permanently. Discovered during PRE2-DOM-01 test design review 2026-06-07.
**Unblock condition:** Add deprecated-key dirty check to `reconcile_stale_tags`
**Source:** PRE2-DOM-01 behavior review 2026-06-07.

---

### TD-043 · `batches.file_count` is always 1 for single-file-created batches — count never updates
**Status:** OPEN
**Phase:** Phase 8 (Briefing)
**Risk if triggered early:** If Phase 8 Briefing reads `file_count` to say "batch of N files processed," single-file-initiated batches will always report 1 even if many files later joined that batch via individual captures. The count is an approximation, not an accurate total.
**What:** When `capture_file()` creates a new batch row for a batch-worthy subfolder (TD-040 fix), it sets `file_count = 1` — the one file being captured right now. Subsequent files captured individually into the same subfolder each update their own `batch_id` FK but do NOT increment `file_count` on the batch row. Only `capture_folder()` (watcher-triggered folder drop) sets an accurate initial count. No mechanism exists to keep `file_count` current for the single-file capture path.
**Why deferred:** `file_count` is informational only in Phase 1–2. No routing, gating, or completeness logic reads it. Accurate counting requires either an UPDATE after every single-file capture (latency) or a reconcile pass (eventual). Neither is worth the complexity until Phase 8 actually consumes the field.
**Unblock condition:** When Phase 8 Briefing needs accurate per-batch file counts, add an UPDATE to `batches.file_count` in the `capture_file()` batch-stamp path, or add a reconcile stage that counts `documents WHERE batch_id = ?`.
**Source:** OQ-BATCH-2 design decision 2026-06-07 — deliberate approximation chosen during TD-040/041 design.

---

### TD-044 · Reconcile must detect and surface stale domain tags in `Projects/<A>/CLAUDE.md`
**Status:** OPEN
**Phase:** Phase 2+ (after Project Registry ships)
**Risk if triggered early:** Project Registry silently moves affected projects to `Uncategorized` when their domain folder is renamed/deleted. Without reconcile, CLAUDE.md keeps a stale `domain/<D>` tag indefinitely; the project stays Uncategorized forever with no user notification.
**What:** When a `Domain/<D>/` folder is renamed or deleted, any `Projects/<A>/CLAUDE.md` that still carries the old `domain/<D>` tag has a stale reference. Reconcile must detect rows where the tag's domain no longer exists as a vault folder, log them, and surface each violation in the Daily Briefing so the user can re-assign the project.
**Why deferred:** Project Registry and Daily Briefing are not yet built. Reconcile cannot surface to Briefing until Briefing's input format is defined (Phase 8).
**Source:** Grill session 2026-06-07 — Project Registry design.

---

### TD-045 · Reconcile must detect and surface missing domain tags in `Projects/<A>/CLAUDE.md`
**Status:** OPEN
**Phase:** Phase 2+ (after Project Registry ships)
**Risk if triggered early:** New projects created without CLAUDE.md (or with CLAUDE.md missing a domain tag) stay in `Uncategorized` indefinitely. Classify can still route to them, but the domain grouping in briefings and search is degraded.
**What:** Reconcile must scan all `Projects/<A>/CLAUDE.md` files and flag any that have no `domain/<D>` tag. Surface each missing mapping in the Daily Briefing so the user can set the domain once.
**Why deferred:** Daily Briefing (Phase 8) is the notification channel; until Briefing's input format is defined, the reconcile stage can be stubbed but not wired end-to-end.
**Source:** Grill session 2026-06-07 — Project Registry design.

---

### TD-046 · Reconcile must detect and surface multi-domain violations in `Projects/<A>/CLAUDE.md`
**Status:** OPEN
**Phase:** Phase 2+ (after Project Registry ships)
**Risk if triggered early:** A project CLAUDE.md with two `domain/<D>` tags violates the one-domain-per-project rule (ADR to be written in design step). Project Registry will pick one domain (first found) silently; the violation is invisible to the user.
**What:** Reconcile must detect `Projects/<A>/CLAUDE.md` files carrying more than one `domain/<D>` tag and surface each violation in the Daily Briefing. The user must decide which domain to keep; the AI must not auto-resolve (design decision).
**Why deferred:** Enforcement requires the ADR to be accepted and the one-domain rule to be mechanically checked. Briefing channel not yet built.
**Source:** Grill session 2026-06-07 — Project Registry design.

---

### TD-047 · Hook regex for removed VaultConfig APIs catches legitimate NoteMetadata field accesses (false-positive)
**Status:** CLOSED (2026-06-07 — regex narrowed to `(vault_cfg|\.vault)\.(attachment_path|archive_path)` in `.claude/settings.json`)
**Phase:** Maintenance (any session touching `reconcile.py`)
**Risk if triggered early:** Every edit to `src/pipelines/reconcile.py` triggers a blocking hook error — "Removed VaultConfig API" — even when the code correctly accesses `note.metadata.attachment_path` (a legitimate `NoteMetadata` field). Causes developer friction and wastes edit cycles investigating a non-violation.
**What:** The hook regex `\.(attachment_path|archive_path)\b` in `.claude/settings.json` matches ANY Python attribute access ending in `.attachment_path` or `.archive_path`, including `note.metadata.attachment_path` at `vault/frontmatter.py:69`. `reconcile.py` Stages 4 and 7 access `note.metadata.attachment_path` legitimately for sibling-binary path resolution. The hook's intent is to catch access to the removed `VaultConfig.attachment_path` / `VaultConfig.archive_path` properties — not NoteMetadata fields.
**Why deferred:** The fix requires narrowing the regex (e.g. `vault_cfg\.(attachment_path|archive_path)` or scoping to variable names typed as `VaultConfig`). Hook regex syntax in `.claude/settings.json` may have limitations on lookaheads; needs brief investigation. Low urgency — false-positive is annoying but edits still persist.
**Unblock condition:** Narrow the hook grep pattern so it only fires for config-object access (`vault_cfg`, `VaultConfig` instances), not arbitrary attribute access.
**Source:** TD-042 implementation 2026-06-07 — hook fired on every edit to `reconcile.py` due to `note.metadata.attachment_path` in Stages 4 and 7.

---

### TD-048 · classify() returns Failure(recoverable=True) but no retry loop exists in pipelines/
**Status:** OPEN
**Phase:** Phase 2 (classify pipeline)
**Risk if triggered early:** If the classify pipeline is built without a retry loop, a transient LLM glitch causes the whole classification to fail with no recovery — the note stays stranded in the inbox with no classification, and no signal that a retry would fix it.
**What:** `classify()` (the pure function in `pipelines/classify.py`) returns `Failure(recoverable=True)` for three cases: provider error, malformed JSON, invalid `target_type`. The `recoverable=True` flag signals that the caller may retry with the same inputs. However, no retry infrastructure exists anywhere in `pipelines/` — no retry count, no backoff, no retry queue. The flag is technically correct but functionally meaningless until the pipeline that calls `classify()` implements a retry loop.
**Why deferred:** The retry loop is the classify pipeline's responsibility, not `classify()`'s. The classify pipeline is the next component to build (separate spec). Logging here so the pipeline spec author sees this as a required element, not an optional enhancement.
**Unblock condition:** When writing the classify pipeline spec, require a bounded retry loop (e.g., max 3 attempts with exponential backoff) around the `classify()` call for `Failure(recoverable=True)`. The loop must also handle the case where all retries are exhausted (fall back to inbox with a CLUELESS-style marker or a human-review queue).
**Source:** Design doc `docs/1_design/phase2/classify.md` §Decisions locked — "TD-pending: Classify returns Failure(recoverable=True) but the pipeline has no retry loop yet". Surfaced during spec writing 2026-06-08.

---

### TD-049 · folder_path NFC mismatch between capture_folder batch insert and capture_file batch lookup
**Status:** OPEN
**Phase:** Phase 2 (classify-inline-capture) — verify at research step
**Risk if triggered early:** For a non-ASCII folder name stored on disk in decomposed (NFD) form, `capture_folder`'s batch insert and `capture_file`'s batch lookup produce different `folder_path` strings → `find_by_folder_path` misses → a second `batches` row is created → files inside get a `batch_id` that does not match the folder's batch row. Breaks batch grouping (Phase 8 Briefing groups by batch). ASCII folder names unaffected.
**What:** `capture_folder` writes `batches.folder_path` via `str(folder_path.relative_to(vault_cfg.root).as_posix())` with **no NFC normalization** (`src/pipelines/capture.py:1556` and the other `_insert_batch` call sites in `capture_folder`). `capture_file`'s batch-stamp pre-step looks up the same folder via `unicodedata.normalize("NFC", str(path.parent.relative_to(root).as_posix()))` (`src/pipelines/capture.py:948`). The two strings diverge whenever the folder name contains decomposed unicode.
**Why deferred:** Pre-existing latent inconsistency; low frequency (only non-ASCII decomposed folder names). Surfaced 2026-06-08 while confirming the "suppress per-file classify on folder-invoked capture" decision in the classify-inline-capture design — the suppress case relies on the folder's `batch_id` reaching each file's row. Folded into that feature's research/spec.
**Unblock condition:** NFC-normalize `folder_path` on BOTH the write path (`capture_folder` `_insert_batch` call sites) and the read path (`capture_file`, already NFC), ideally via one shared helper so they can never drift. Verify at the research step of the classify-inline-capture feature.
**Source:** classify-inline-capture design 2026-06-08 — design doc `docs/1_design/phase2/classify-inline-capture.md` Risk R6.

---

### TD-052 · Pre-R1 binary-sibling notes keep filename titles; backfill needs re-capture, not reindex
**Status:** OPEN
**Phase:** Post-Phase-3 (after the R1 descriptive-title capture fix ships)
**Risk if triggered early:** None to data — purely a result-quality gap. Binary-sibling notes captured before the R1 fix continue to show their filename (e.g. `report.pdf`) as `documents.title` and on search cards, weakening triage signal for the Phase-4 MCP AI until they are re-captured.
**What:** The R1 fix (A5 resolution, `docs/1_design/P3_session_b_query_path.md` §Revision R1) wires the AI descriptive title onto new captures' frontmatter and `documents.title`. Notes captured before R1 have no `title:` in frontmatter, so their `documents.title` stays the filename stem. `--reindex` reads frontmatter and re-runs only the index writers (`index_embedding`/`index_keywords`); it does **not** call the summarizer/`extract_metadata` LLM stage (`capture.py:215`), so it **cannot** regenerate an AI title for a note whose frontmatter never had one. `_derive_title` (`storage/documents.py:69`) therefore keeps falling back to `Path(vault_path).stem` for those rows.
**Why deferred:** Backfilling requires re-capture (the full `summarize → metadata → store` pipeline) on each original file — an LLM call per file. That is heavier than a reindex and is out of scope for the R1 capture fix, which only corrects forward-going captures. Recorded so a future maintainer knows reindex will not fix old siblings.
**Unblock condition:** Add a one-off backfill task (or a `--recapture` mode) that re-runs the full capture pipeline on pre-R1 binaries so their siblings gain an AI descriptive title; or accept the filename titles for legacy siblings as good-enough.
**Source:** P3 Session B design Revision R1, 2026-06-10 — `docs/1_design/P3_session_b_query_path.md` §Revision R1 Decision 5 (backfill story); A5 invalidation in `docs/3_research/P3_session_b_query_path.md`.

---

### TD-063 · Catch-up scan enqueues all doc ids in one burst
**Status:** OPEN
**Phase:** During/after Phase 8 Slice A implementation (revisit if vault grows large)
**Risk if triggered early:** None for the target single-user personal vault (hundreds of docs). Only matters at scale — a vault with thousands of unclassified docs floods the in-memory `asyncio.Queue` at container startup in one burst, spiking memory and starving the event loop before the sequential consumer drains it.
**What:** Phase 8 Slice A's startup catch-up scan runs the work-discovery query and `put`s every discoverable doc id onto the queue at once (one burst), rather than paging/batching the enqueue. Chosen deliberately for simplicity (OQ-P8A-03 resolved: one burst for Slice A).
**Why deferred:** One-burst is the simplest correct design and fine for the target deployment. Paging adds code for a case that may never occur in single-user use. Recorded so a future maintainer knows where to add back-pressure if a large backlog becomes real.
**Unblock condition:** Page/batch the catch-up scan's `put`s (bounded queue + producer that awaits capacity, or chunked enqueue) if measured startup backlog causes memory/latency problems.
**Source:** OQ-P8A-03; Phase 8 Slice A plan `docs/4_plans/phase8_sliceA_classify_infra.md`; design Risks "Sequential-consumer back-pressure".

---

### TD-064 · Dynamic per-dimension fact summary may strain token budget as facts accumulate
**Status:** OPEN
**Phase:** Phase 9/10 (revisit when context-injection token cost is measurable)
**Risk if triggered early:** None now — the ranker caps each dimension at `max_entries_per_dimension`. Over time, as facts accumulate across many dimensions, the live (per-run) assembly of dimension summaries may strain the housekeeping AI's token budget and raise classify cost.
**What:** Phase 8's Context Loader assembles a per-dimension summary of existing facts live on every classify run (ranked + capped). It is recomputed each run rather than pre-computed/cached. Tied to the deferred OQ-P8A-04 (periodic synthesis "fact sheet" for the retrieval path).
**Why deferred:** Dynamic assembly is always-current, needs no extra LLM cost, and preserves per-fact granularity (source/trust/status + entry `id` for update/retire). Pre-computing is premature before token cost is measured and before any retrieval consumer (Phase 9) exists. The structured `knowledge_entries` remain source of truth regardless — any summary is a derived cache.
**Unblock condition:** If measured token cost bites, optimize from dynamic assembly to pre-computed per-dimension summaries (periodic synthesis session). See OQ-P8A-04.
**Source:** grill tech-debt + Phase 8 Slice A design; OQ-P8A-04 (`OPEN_QUESTIONS.md`).

---

### TD-065 · Catch-up scan enqueues all discoverable ids in one burst
**Status:** OPEN
**Phase:** Phase 8 Slice B or later
**Risk if triggered early:** For very large vaults (thousands of unclassified documents), `catch_up_scan()` loads all ids into memory via `queue.put_nowait()` in a single burst at startup. This could cause memory pressure on container boot.
**What:** Phase 8 Slice A's `catch_up_scan()` (`src/pipelines/classify.py`) calls `find_unclassified()` and immediately enqueues every returned id with `queue.put_nowait()`. The `asyncio.Queue` has no maxsize. For a vault with very few docs this is fine; for a vault with thousands of pending docs, this loads all ids into memory at once before the single sequential consumer can begin processing.
**Why deferred:** Slice A is infrastructure-only (no AI calls, fast consumer). The queue drains quickly. Memory pressure is a theoretical concern until Slice B adds AI latency. Page/batch the enqueue then.
**Unblock condition:** When Slice B adds AI calls (increasing per-doc latency), add a cap (e.g. 1000) and a mechanism to re-scan for remaining work, or use a bounded queue.
**Source:** Phase 8 Slice A plan OQ-P8A-03; `src/pipelines/classify.py::catch_up_scan()`.

---

## Archive

### TD-001 · core/pipeline.py
**Status:** RESOLVED
**Phase:** Phase 0 ✅ (delivered 2026-05-14)
**Risk if triggered early:** N/A
**What:** `core/pipeline.py` was deferred from the storage plan scope.
**Why deferred:** Out of scope for storage_level plan; delivered in Phase 0.
**Source:** Out of Scope, `plans/storage_level.md`

---

### TD-002 · llm/prompt_loader.py and prompts/
**Status:** RESOLVED
**Phase:** Phase 0 ✅ (delivered 2026-05-14)
**Risk if triggered early:** N/A
**What:** `llm/prompt_loader.py` and `prompts/` (empty scaffold) were deferred from the storage plan scope.
**Why deferred:** Out of scope for storage_level plan; delivered in Phase 0.
**Source:** Out of Scope, `plans/storage_level.md`

---

### TD-003 · vault/ (paths, frontmatter, reader, writer)
**Status:** RESOLVED
**Phase:** Phase 0 ✅ (delivered 2026-05-20 — confirmed in STATE.md Phase 0 checklist)
**Risk if triggered early:** N/A
**What:** `vault/` module (paths.py, frontmatter.py, reader.py, writer.py) was deferred from the storage plan scope.
**Why deferred:** Outside storage scope; built in Phase 0.
**Source:** Out of Scope, `plans/storage_level.md`

---

### TD-014 · write_note field clearing — Option B merge removed
**Status:** RESOLVED (2026-05-20)
**Phase:** ✅ Resolved
**Risk if triggered early:** N/A
**What:** Removed Option B merge from `vault/writer.py`. `write_note` now writes exactly what the caller passes. Pipelines must `read_note` first if they want to preserve existing fields. Only `created` is preserved automatically. See C-03.
**Why deferred:** Was a design ambiguity; resolved with explicit pure-writer contract.
**Source:** `plans/vault_layer.md` Phase 4 Option B note

---

### TD-022 · capture.py / cli/main.py .root/.attachment_dir workaround callers
**Status:** RESOLVED (Brief #3 Phase 1, 2026-05-24)
**Phase:** ✅ Resolved
**Risk if triggered early:** N/A
**What:** Three callers in `capture.py:456`, `capture.py:627`, `cli/main.py:127` used `.root / .attachment_dir` with `# COUPLING:` comments as temporary workaround. All three retired.
**Why deferred:** Awaited Brief #2/#3 to replace with `project_attachment(name)` / `domain_attachment(name)` helpers.
**Source:** `docs/plans/revise_attachment_layout.md` Phase 1 Notes

---

### TD-023 · vault/watcher.py single attachment_path constructor arg
**Status:** RESOLVED (Brief #3 Phase 1, 2026-05-24)
**Phase:** ✅ Resolved
**Risk if triggered early:** N/A
**What:** Watcher took a single `attachment_path: Path` arg to skip events. Replaced with `vault_config: VaultConfig` and `_is_in_managed_attachment` for per-project path check.
**Why deferred:** Awaited Brief #3 Phase 1.
**Source:** `docs/plans/attachment_sync_and_archive.md` Phase 1

---

### TD-024 · vault/indexer.py::scan_non_md_drops single-path skip
**Status:** RESOLVED (Brief #2 Phase 4, 2026-05-24)
**Phase:** ✅ Resolved
**Risk if triggered early:** N/A
**What:** `scan_non_md_drops(root, attachment_path: Path)` used a single-path skip. Replaced with `vault_config` signature using `_is_in_managed_attachment` + `_has_inbox_sibling` rules.
**Why deferred:** Awaited Brief #2/#3 design finalization.
**Source:** `docs/plans/revise_attachment_layout.md` Phase 3 Notes

---

### TD-025 · tests/test_vault/test_indexer.py old scan_non_md_drops signature
**Status:** RESOLVED (Brief #2 Phase 4, 2026-05-24)
**Phase:** ✅ Resolved
**Risk if triggered early:** N/A
**What:** 6 old tests used `scan_non_md_drops(root, attachment_path: Path)` signature. All updated to new signature + 4 new tests added.
**Why deferred:** Awaited TD-024 resolution.
**Source:** `docs/plans/attachment_capture_pipeline.md` Phase 4 Steps

---

### TD-026 · Orphan reconciliation — binary with no sibling .md
**Status:** RESOLVED (Brief #3 Phase 4, 2026-05-24)
**Phase:** ✅ Resolved
**Risk if triggered early:** N/A
**What:** No reconciliation pass for binaries in `attachment/` with no corresponding sibling `.md`. Resolved by `reconcile` Stage 2 (captures binaries with no sibling) and Stage 4 (walks `.summaries/`, unlinks ghosts, removes DB rows).
**Why deferred:** Awaited Brief #3 Phase 4.
**Source:** `docs/plans/attachment_sync_and_archive.md` Phase 4

---

### TD-027 · prompts/summarize_attachment.yaml missing
**Status:** RESOLVED (Brief #2 Phase 2, 2026-05-24)
**Phase:** ✅ Brief #2 Phase 2
**Risk if triggered early:** N/A
**What:** `prompts/summarize_attachment.yaml` was missing; needed for non-md binary capture.
**Why deferred:** Awaited Brief #2 Phase 2.
**Source:** `docs/plans/attachment_capture_pipeline.md` TD-C8

---

### TD-028 · ClaudeCliProvider metadata JSON parse fails on short DOCX extracts
**Status:** RESOLVED (2026-05-25)
**Phase:** ✅ Resolved
**Risk if triggered early:** N/A
**What:** Two-layer fix: (1) `_parse_metadata_json` now catches `JSONDecodeError` and returns `Success({"title": source_stem, "tags": []})` instead of hard `Failure`; (2) `prompts/extract_metadata.yaml` system prompt updated with explicit thin-content instruction — LLM told to return minimal JSON rather than refusing. 4 tests updated/added.
**Why deferred:** Deferred as TD from Brief #3; fixed 2026-05-25.
**Source:** STATE.md "Re-make work" §1

---

### TD-030 · Critical — vault/watcher.py::on_deleted binary sync ran after _should_skip
**Status:** RESOLVED (Brief #4, 2026-05-24)
**Phase:** ✅ Resolved
**Risk if triggered early:** N/A
**What:** `on_deleted` called `_should_skip(path)` before binary-sync dispatch, silently skipping sibling cleanup for managed-attachment deletes. Reordered: `_handle_binary_delete` now runs before `_should_skip` filter. Regression test added.
**Why deferred:** Found in code review 2026-05-24; fixed same day.
**Source:** Code review 2026-05-24 (Critical #1)
