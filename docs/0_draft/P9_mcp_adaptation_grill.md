# Phase 9 — MCP Adaptation: Grill Output (v3 — COMPLETE)

_Created: 2026-06-15. Revised: 2026-06-15 (v2, fact-checked). Finalized: 2026-06-15 (v3, all decisions locked, edge cases probed, signed off)._
_Status: **GRILL COMPLETE.** All decisions locked. Edge cases probed. Ready for design→spec→research→plan._
_Audience: Next AI session running `/build-pipeline` Phase 9 (design step onward)._

> This v3 supersedes v2. v2 left L1 (human-correction guard), L3 (ranker ambiguity), Step 4 (edge
> cases), and Step 5 (sign-off) open. This session resolved all of them + grilled Phase 9 tech
> debts and bug fixes.

---

## Scope (confirmed)

Phase 9 adapts the existing MCP server (`src/mcp_server/`, **9 files, ~1931 lines** — incl. `api.py` + `cloud_entry.py`, the cloud REST + bootstrap layer already shipped in P5 Slice 2) to the cloud-native model.

**Depends on (ALL COMPLETE — verified):** Phase 5 (DB schema, `knowledge_entries` shipped migration 008), Phase 7 (capture populates `documents.full_body` + `documents.summary`), Phase 8 (classify populates `knowledge_entries` — extraction pipeline built + merged; **classify.py decomposed into 4 files + documents_classify.py extracted**).

---

## Fact-check corrections (v2 session) — read before trusting any prior P9 doc

| Prior claim (stale) | Corrected truth |
|---|---|
| "P5 Slice 1 PLANNED, `knowledge_entries` doesn't exist yet" (handoff) | **FALSE.** Table shipped in migration `008` (2026-06-13). Phase 8 Slice B extraction pipeline (entity extractor, entry writer, `query_by_entity`, `confidence_to_status`, retry/park, source-prune) built + merged. |
| "MCP = 7 files / ~1200 lines" | **STALE.** 9 files / ~1931 lines. Missing from prior count: `api.py` (555), `cloud_entry.py` (177) — the cloud REST + bootstrap layer. |
| "old context model reads CLAUDE.md from disk + concentration-gates" | **TRUE — still current code** in `mcp_server/context.py` (read CLAUDE.md, concentration gate, cap, hash-dedup). This is genuinely what Phase 9 rewrites. |
| Q8 server bootstrap = open | **ANSWERED.** Solved in shipped `cloud_entry.build_app()` + composed-lifespan worker (ADR-0017). |
| Q11 `kms_read` source = open | **ANSWERED.** Current: vault disk via `read_note()`. Target: `documents.summary` (DB). |
| Q9 tool list "net 8" | Current baseline = **5 MCP tools**; roadmap-scoped end state was 4. New surface (below) changes this again. |

**`knowledge_entries` actual schema (13 cols, verified):** `id` (int PK, AUTOINCREMENT, rename-stable), `dimension`, `entity`, `tag`, `fact`, `status` (`pending`/`confident`/`retired`), `confidence` (REAL), `sources` (TEXT — JSON array of **integer doc-id strings**, e.g. `["12","47"]`), `reasoning`, `created_at`, `updated_at`, `trust_score` (REAL, default 0.5), `retrieval_count` (INT, default 0 — reinterpreted as `retrieval_score` REAL in Phase 9, see decision L3).

**`documents`:** has both `id` (INT PK, rename-stable) AND `vault_path` (UNIQUE). `summary` = structured 5-section Markdown digest (Overview / Key points / Decisions / Action items / People mentioned). `full_body` = raw verbatim text. Cloud upload path writes **DB rows + blob bytes only — no .md file, no frontmatter** (frontmatter lives on the laptop, owned by the daemon). **No `get_by_id` lookup exists** — only `get_by_path(vault_path)`. Phase 9 adds `get_by_id`.

**Phase 8 decomposition (COMPLETE before Phase 9):**

| File | Lines | Role |
|---|---|---|
| `classify.py` | 222 | Public API: content_reader, context_loader, consumer, catch_up_scan |
| `classify_extract.py` | 184 | Entity extraction + validation |
| `classify_writer.py` | 304 | Entry writing + DRY helpers |
| `classify_orchestrator.py` | 309 | Orchestration + retry loop |
| `documents_classify.py` | 194 | Classify-specific DB helpers |
| `documents.py` | 639 | General document DB layer (down from 816) |

---

## Locked decisions (all finalized)

### 1. `kms_move` — DELETE — **LOCKED**
System never moves files. Delete `_move.py`. Remove `kms_move` from `tools.py` + `AI_INSTRUCTIONS.md`.

### 2. `kms_correct` — **LOCKED (Phase 9)**
Separate tool. Patches an existing `knowledge_entries` row by id (reuses `upsert`/`retire`) — ops: edit fact / change tag / change entity / promote (pending→confident) / retire (with required reason). **Existing entries only** (new info → `kms_write`). Immediate effect. Logs each correction to **`audit_log`** (`pipeline="correct"`). Consumer AI routes correct-vs-write; server hard floor = valid `entry_id` required (else `Failure`).

**Trust_score movement and classify guard = DEFERRED to Phase 10** (nothing ships to users until Phase 10 is done — no gap risk). Phase 9 does NOT wire `adjust_trust()` or the classify overwrite guard. Phase 9 DOES structure `classify_writer.py` with an explicit decision point (`_should_overwrite()` or equivalent) so Phase 10 slots the guard in cleanly.

**Un-retire gap:** No explicit retired→confident op. User can achieve same effect via `kms_write` (creates new entry with correct fact). Design/spec may add an "un-retire" op if warranted.

### 3. `kms_write` — **LOCKED (Phase 9)**
ADR-0011 conflict found **STALE** (its premises — vault writes + frontmatter — are dead; TD-056 self-marked "do not implement as written"). Real gap: consumer had no way to persist a chat insight. Tool sends content → **cloud summarizes** (option A — one pipeline, uniform 5-section summary) + classify, `source_type=chat_session`. Consumer may pass a title/intent hint (steers, does not replace the summary). Creates a new document + facts.

**No content-level dedup.** Near-duplicate `kms_write` calls create separate documents. Fact-level dedup in classify (twin-lookup on entity+dimension+tag) prevents duplicate facts. Document-level dedup is a future nice-to-have, not a correctness issue.

### 4. Pending requests (`kms_pending_requests` + `kms_resolve_request` + table) — **DEFERRED to Phase 10 (whole thing)**
Owner decided: defer the entire pending-request system — consumer read/resolve tools AND the table AND the housekeeping creation logic — to Phase 10. Nothing in Phase 9.
**Ripple:** `kms_vault_info` drops its "pending interactive requests" component in Phase 9.

### 5. Scheduled session — Claude Desktop native — **LOCKED**
Scheduling setup = user's responsibility, out of Phase 9 scope.

### 6. `synthesized_context` table — **DROPPED**
Contradicted resolved OQ-P8A-04 (dynamic budget-capped fact-assembly chosen over pre-synthesized prose sheets). Orientation now comes from dynamic fact-assembly (bullet facts), code-assembled at request time from `knowledge_entries`, always current, no staleness, no CLAUDE.md, no prose table.

### 7. Context injection — **LOCKED**
OLD (still current code): read CLAUDE.md from disk, gate by domain concentration, cap, hash-dedup.
NEW (cloud-native): inject `knowledge_entries` fact bullets — code-assembled from DB, ranked, budget-capped, concentration gating **dropped**, conversation-level dedup kept, **zero CLAUDE.md disk reads anywhere.**

### 8. Three-tier retrieval — **LOCKED** → see retrieval surface below.

### 9. AgentBase deployment — **LOCKED**
- System is **single-tenant per deployment** (no `user_id`/`tenant_id` anywhere). One instance = one vault + one DB.
- **User isolation = separate deployments** (each tester: own container + DB + vault + own daemon key + own gateway endpoint). NOT a shared instance.
- **MCP gateway requires auth (IAM at the AgentBase Resource Gateway) — never NONE.** Inbound auth handled by the platform gateway, not our code (our code only gates `/api/*` for the daemon via `KMS_DAEMON_API_KEY`).
- **Deliverable = 2-part non-technical guide:** builder part (stand up deployment, drop in IAM/API/daemon keys, configure gateway auth) + tester part (connect Claude Desktop, run daemon).

### 10. `AI_INSTRUCTIONS.md` rewrite — **LOCKED**
Rewrite the consumer's operating manual: remove all move instructions; document the 5-tool surface; explain the facts-vs-summary model (facts = targeted extracted insight, summary = general 5-section digest); explain correct-vs-write routing (correct = fix existing by id; write = add new). **Behavioral stance:** `kms_write` = **proactive + transparent** (save clearly-valuable insights without a blocking question, tell the user it saved; ask only when ambiguous). `kms_correct` = **confirm-first** (propose the change, act on user OK — it mutates trusted knowledge).

### 11. L1 — Human-correction protection — **LOCKED (split Phase 9/10)**

**Problem:** Classify entry-writer overwrites facts unconditionally. `kms_correct` corrections evaporate when classify re-processes a source document.

**Resolution:**
- **Phase 9:** `kms_correct` patches entry + logs to `audit_log` (`pipeline="correct"`). Trust_score untouched. Classify overwrites unconditionally. `classify_writer.py` structured with explicit decision point for Phase 10 to slot guard into.
- **Phase 10:** `adjust_trust()` wired (promote +0.05, retire -0.10, edit → 0.6; config-driven per C-06). Classify guard: `trust_score > 0.5` → do NOT overwrite. Instead write conflicting new fact as a **separate `pending` entry** with reasoning "Contradicts human-corrected entry #N". Conflict logged in BOTH:
  - Entry `reasoning` field (human-readable context)
  - `audit_log` with `outcome="conflict"` (machine-queryable for Phase 10 web UI conflict queue)
- **Design principle:** AI owns facts it wrote (trust_score = 0.5). Humans own facts they touched (trust_score > 0.5). Ownership transfers on first human touch. No permanent freeze — new evidence surfaces as competing entry, user resolves.

### 12. L3 — Ranker + retrieval_score — **LOCKED**

**Ranker ORDER BY (4-key):** `trust_score DESC, retrieval_score DESC, confidence DESC, updated_at DESC`

Keeps `confidence` (the only key that currently differentiates entries — trust and retrieval are 0/0.5 everywhere until Phase 10).

**`retrieval_count` → `retrieval_score` (decaying):**
- Column reinterpreted as decaying REAL score (SQLite type-flexible; INT 0 is valid REAL 0.0).
- On each injection (entry surfaced in MCP tool response): `retrieval_score = retrieval_score * decay_factor + 1.0`
- Periodic decay sweep: `UPDATE knowledge_entries SET retrieval_score = retrieval_score * decay_factor` (daily/weekly, config-driven frequency + decay_factor).
- **Why decay:** Prevents rich-get-richer feedback loop. A fact surfaced early by bad query luck climbs and stays sticky without decay. With decay, influence fades if nobody asks about it.
- No new table. No retrieval log. Migration may rename column or Phase 9 just starts treating the existing INT as REAL.

---

## Retrieval tool surface — LOCKED

### Terminology (selection-time roles, NOT stored attributes)
- **Query facts** — `knowledge_entries` whose content MATCHES the query. (Needs facts to be searchable — new work.)
- **Background / orientation facts** — top facts for the entities *in scope*, selected by ranker, **independent of query text**.
- **No "background" flag on facts.** The same fact is background in one query and a query-hit in another → role assigned at retrieval time.

### `kms_vault_info`
Structural overview + **orientation fact bullets**. Always injected. No CLAUDE.md. Pending-interactive-requests component DEFERRED to Phase 10.
**Amount:**
- **Structural map = all entity names grouped by dimension + per-dimension counts.** If a dimension is huge → top-K names by rank + "+N more (use `kms_search`)". Cap config-driven (`max_entities_per_dimension`).
- **Orientation facts = per-dimension top-N** (small N, balanced coverage across dimensions) **+ per-dimension cap as backstop.** NOT global top-N (one busy dimension would swamp it); NOT recent-only. Ranked trust → retrieval_score → confidence → recency. N + cap config-driven (`max_orientation_facts_per_dimension`).
- **No separate global token budget.** Two config knobs (entity cap + orientation cap) are sufficient. Add global backstop later only if needed in practice.

### `kms_search` — collapses old `kms_read` in; returns TWO tiers in one call
- **Orientation facts** (entity-derived, ranked, capped).
- **Tier 1 — query facts**: hybrid semantic+keyword match on fact text. Each carries its **source doc reference(s)**.
  - **Research gate:** Phase 9 research step must test short-fact embedding separation before locking ranking weights. If embeddings don't separate well on one-liners, lean heavier on FTS keyword matching in the merge formula. Config-driven weight between the two.
- **Tier 2 — structured summaries** of the **top-K** source docs (capped to bound tokens).
- **Searches BOTH corpora** (facts + documents independently), merged. Document search (on the structured summary, which exists at capture time) is the **recall safety-net** so freshly-captured-but-not-yet-classified docs still surface.
- **Fact↔summary content duplication ALLOWED** (holistic view).
- `kms_read` **removed** as a separate tool — its job splits between search (top-K summaries) and `kms_inspect summary` mode (on-demand).

### `kms_inspect` — drill-down by reference
- **Batched references**, **uniform modes per call** (Shape A).
- Modes: `summary` (structured summary) / `text` (raw `full_body`) / `file` (local vault path, laptop-dependent).
- Default `summary` if unspecified. **`text` is opt-in + capped** (heavy; never forced; cap how many refs may request `text` in one call).
- Canonical reference = integer **`id`** (rename-stable; `vault_path` changes on move).
- `summary`/`text` always work from DB; only `file` is laptop-dependent — tool returns the path regardless, consuming AI handles availability. `AI_INSTRUCTIONS.md` teaches consumer "tier 3 requires laptop to be open."

### Phase-9 MCP tool list
**5 tools:** `kms_vault_info`, `kms_search`, `kms_inspect`, `kms_write`, `kms_correct`. **Removed:** `kms_read` (folded into search), `kms_move` (system never moves files).

### Dedup rule
> **Dedup by IDENTITY, allow overlap by CONTENT.**
> - Same fact row, or same document → show **once** (identity).
> - A fact and a summary that *describe* the same thing → keep **both** (content overlap = holistic value).
> - Conversation-level: don't re-inject an orientation fact already sent earlier in the same conversation.

---

## Edge cases probed (Step 4)

| Scenario | Resolution |
|---|---|
| `kms_write` near-duplicate content (user saves same insight twice) | No content-level dedup in Phase 9. Classify twin-lookup dedupes facts. Two similar documents in DB is harmless — search identity-dedup prevents showing both. |
| `kms_correct` with nonexistent entry ID | Clear `Failure` returned. Existing `upsert` checks `cursor.rowcount` (P5 Slice 1 note #6). Tool shim passes error through to consumer AI. |
| Search before classify finishes (fresh doc, no facts yet) | Covered by dual-corpus search. Document summary searchable immediately after capture. Facts appear when classify completes. |
| `kms_inspect file` when laptop closed | Tool returns vault path anyway. Consuming AI handles availability. `AI_INSTRUCTIONS.md` documents tier 3 = laptop-dependent. |
| Consumer AI mis-routes correct vs write (L12) | Self-healing. If routed as `kms_write`, classify twin-lookup folds into existing entry. If it misses, user sees both in web UI and resolves. `AI_INSTRUCTIONS.md` teaches routing rule. Reversible either way. |
| Fact hybrid index — short facts in embedding space (L6) | **Research gate.** Build both embedding + FTS keyword index. Research step must verify short-fact separation before locking ranking weights. Fallback: lean on keyword matching, config-driven weight. |
| `kms_vault_info` size explosion (many entities) | Config-driven caps sufficient (`max_entities_per_dimension`, `max_orientation_facts_per_dimension`). No global token budget needed. Add later if practice demands it. |
| `kms_correct` on retired entry (un-retire) | No explicit retired→confident op. Workaround: `kms_write` creates new entry with correct fact. Design/spec may add "un-retire" op if warranted. |
| Retrieval_score runaway (rich-get-richer) | Decaying score prevents it. See decision L3. |

---

## Bug fixes bundled in Phase 9

| ID | Severity | Description | Fix |
|---|---|---|---|
| L2 | BUG | `cloud_entry.py` lost `if __name__ == "__main__": uvicorn.run(...)` (removed P7B commit `1b1f33d`). `scripts/start.sh` → starts nothing. | Restore entry block + add boot test. |
| C1 (P7.5) | LOW | `api.py:62` re-reads `KMS_DAEMON_API_KEY` from `os.environ` on every request. | Read once at startup. |
| C2 (P7.5) | MEDIUM | `capture.py:342` calls sync `blob_store.put()` from async handler. Blocks event loop. | Switch to `async_put` (already exists at `blobs.py:316`). |
| C3 (P7.5) | MEDIUM | `api.py:330` `_delete_with_blob_cleanup` is sync, called from async handler. | Wrap in `asyncio.to_thread()` or make fully async. |
| C4 (P8 review) | CRITICAL | Worker task cancelled but never awaited in `cloud_entry.py:129`. | **ALREADY FIXED** in uncommitted changes — `await worker` after cancel. |
| M10 (P8 review) | LOW | Unused `import asyncio` in `api.py`. | **ALREADY FIXED** in uncommitted changes. |

---

## Build costs (for design/spec)

1. **Fact hybrid index** — embeddings + FTS keyword on `knowledge_entries` + new migration. Re-embed a fact on edit. Research gate: confirm short one-line facts separate well in embedding space.
2. **`get_by_id`** lookup on `documents` (doesn't exist today).
3. **Expose `id`** on search result cards (currently only `vault_path`) so facts and doc results share one canonical handle.
4. **`retrieval_score` decay** — increment on injection + periodic sweep + ranker update (4-key ORDER BY).
5. **Search corpus merge + identity-dedup** across facts + documents.
6. **Restore uvicorn entry block** + boot test.
7. **Async fixes** (C2: `async_put`, C3: `to_thread`).
8. **API key read-once** (C1).
9. **`kms_correct` pipeline** — tool shim + entry patch + audit_log write.
10. **`kms_write` pipeline** — tool shim + capture pipeline invocation + `source_type=chat_session`.
11. **Context injection rewrite** — `knowledge_entries` fact bullets, ranked, budget-capped, conversation-dedup. Zero CLAUDE.md reads.
12. **`_resolve.py` rewrite** — 3-tier model from DB.
13. **Delete `_move.py`** + remove `kms_move` from tools.py.
14. **`AI_INSTRUCTIONS.md` rewrite** — 5-tool surface, facts-vs-summary model, correct-vs-write routing.
15. **Decision point in `classify_writer.py`** — explicit `_should_overwrite()` for Phase 10 to slot trust guard into.
16. **AgentBase deployment guide** — 2-part (builder + tester).

**Out of scope:** multi-tenancy (one instance, many isolated vaults) — would need tenant column on every table. Isolation stays per-deployment.

---

## Notes for Phase 10 (logged per owner request)

### A. Corrections / self-learning (from decisions 2 + 11)
1. **Phase 9 does:** `kms_correct` patches entry + logs to `audit_log` (`pipeline="correct"`). No trust movement. No classify guard.
2. **Phase 10 must build:**
   - **`adjust_trust(current, action) → float`** pure function. Promote: +0.05, retire: -0.10, edit: set 0.6. Asymmetric. Deltas in config (C-06).
   - **Classify guard** in `classify_writer.py` `_should_overwrite()`: if `trust_score > 0.5`, write conflicting fact as new `pending` entry instead of overwriting. Log conflict in entry `reasoning` + `audit_log` (`outcome="conflict"`).
   - **`corrections` table reshape** — exists but inert + wrong-shaped (FK `document_id` only). Reshape via migration (designed schema in `docs/0_draft/self-learning-research.md:167-184`). If structured few-shot consumption wanted, backfill from `audit_log`.
   - **Few-shot injector** — load recent corrections → prepend to extraction prompt. Separate from `previous_attempt_feedback` slot (ADR-0018 = machine validation-error slot).
   - **`min_trust` filtering activation** — config `mcp.context_injection.min_trust: 0.3` starts excluding entries once corrections move trust scores.
   - **Volatility flag** — entries with > 3 corrections get `[frequently corrected]` appended in context blocks.
   - **Web UI + COMMENT feature** + conflict queue (surfaces `audit_log WHERE outcome = 'conflict'`).

### B. Pending requests (decision 4)
3. Entire pending-request system (consumer tools + table + housekeeping creation logic) = Phase 10.

### C. Ranker evolution
4. `retrieval_score` increment + decay = **Phase 9**. Trust_score *movement* = Phase 10.
5. `min_trust` filtering = Phase 10. Phase 9 ranks all non-retired (all start at trust 0.5).

---

## MCP spec findings (researched 2026-06-15 — still valid)
- MCP is **pull-only** — server cannot push to client. No native task dispatch.
- **Sampling** (`sampling/createMessage`) — server requests LLM completion from client. Human-in-the-loop. Not task dispatch.
- **Resource subscriptions** — server notifies client of resource change; client re-reads. Possible future enhancement, not MVP.
- **Elicitation** (`elicitation/create`) — server requests user input via forms. For data collection, not task dispatch.

---

## CONTEXT.md updates needed
- **query facts** / **orientation (background) facts** — selection-time roles, not stored attributes. Same fact can be either depending on the query.
- **retrieval_score** — decaying score on `knowledge_entries` representing how often a fact is surfaced. Incremented on injection, periodically decayed. Prevents stale-but-once-popular facts from dominating ranking.

---

## Landmines / risks (verified — for design/spec)

| ID | Status | Description |
|---|---|---|
| L1 | **RESOLVED** | Classify overwrites human corrections → Phase 9 adds decision point, Phase 10 wires guard. |
| L2 | **FIX IN P9** | Container boots nothing — restore `__main__` block. |
| L3 | **RESOLVED** | Ranker 4-key with decaying retrieval_score. |
| L4 | **OPEN** | Identifier mismatch — facts→docs by integer `id` (`sources=["12","47"]`); search results expose `vault_path`; no `get_by_id`. Add `get_by_id` + `id` on cards. |
| L5 | **NOTED** | Single-tenant only. Separate testers = separate deployments. |
| L6 | **RESEARCH GATE** | Facts not searchable yet. Need hybrid index. Research must verify short-fact embedding separation. |
| L7 | **MITIGATED** | Recall gap — also search `documents.summary` + identity-dedup. |
| L8 | **NOTED** | Semantic search sees summary only. `full_body` is keyword-only, never embedded. |
| L9 | **NOTED** | Inbound MCP gateway auth = platform-specific. Confirm mechanics with organizer/BTC. |
| L10 | **NOTED** | Stale source docs (STATE.md, roadmap acceptance criteria list `kms_read` — gone). |
| L11 | **PHASE 10** | `corrections` table inert + wrong-shaped. Phase 9 logs to `audit_log`. |
| L12 | **MITIGATED** | `kms_correct` mis-route → classify twin-lookup dedupes. `AI_INSTRUCTIONS.md` teaches routing. |
| L13 | **NOTED** | Migration cascade — new migration bumps prior version-pin tests. |
| L14 | **NOTED** | Binary docs: `summary` == `full_body` (vision description in both); `kms_inspect text` returns description, not raw bytes. |

---

## Sign-off checklist

- [x] All 12 decisions locked (1–10 from v2 + L1 + L3)
- [x] Retrieval tool surface locked (5 tools, dedup rule, ranker)
- [x] Edge cases probed (9 scenarios)
- [x] Bug fixes scoped (6 items, 2 already fixed)
- [x] Build costs enumerated (16 items)
- [x] Phase 10 handoff notes written
- [x] Research gates flagged (short-fact embedding separation)
- [x] CONTEXT.md updates identified
- [x] Landmines catalogued with status

**Grill status: COMPLETE.** Ready for `/build-pipeline` design step.
