# Additive rearchitecture — defer breaking changes to the phase that rewrites the consumer

_Created: 2026-06-12_

The cloud-native rearchitecture (Phases 5–10) was originally scoped so that Phase 5 ("Infrastructure Foundation") would redesign `documents.upsert()`, split the config, and delete the dead vault modules up front. Investigation during the Phase 5 Slice 1 grill showed every one of those changes is a **breaking change to live code owned by a later phase** — so doing them in Phase 5 either breaks the repo or drags Phase 6/7/9 work forward. **Decision: the rearchitecture proceeds additively. A breaking change (deleting a module, changing a signature, removing vault root from config) happens in the phase that rewrites the change's last live consumer — never earlier. Each phase keeps the repo importable and the existing test suite green; the big test-breakage (rearch doc §27) is absorbed phase-by-phase as consumers are rewritten, not in one foundation pass.**

**Status:** accepted

## Context — the collision

Phase 5's roadmap text called for three breaking actions. Each collides with a live consumer:

- **Retire dead modules** (`vault/writer.py`, `frontmatter.py`, `reader.py`, `indexer.py`, `move_guard.py`, `mcp_server/_move.py`) — still imported by `pipelines/capture.py` (Phase 7), `vault/watcher.py` (Phase 6), `pipelines/reconcile.py` (unassigned), `cli/main.py`, `handlers/markdown_handler.py`, `vault/registry.py`, and all of `mcp_server/` (Phase 9).
- **Redesign `documents.upsert()`** to take a structured summary + `full_body` instead of `WriteOutcome` — its only caller is `capture.py` (Phase 7), and no new caller exists in Slice 1.
- **Config split** (remove vault root from cloud config) — vault-root config has 49 live usages across 7 files, all owned by Phases 6/7/9.

## Decision — the sequencing contract

1. **Additive-only per slice.** A slice adds new tables/columns (nullable), new modules, new config files. It does not alter or delete anything a live consumer still uses.
2. **A breaking change rides with its last consumer.** A module/signature/config dies only when the LAST live consumer of it has been rewritten. Mapping recorded on the roadmap:
   - `writer`/`frontmatter`/`indexer` → die across Phase 6 (watcher→daemon) + Phase 7 (capture rewrite).
   - `reader` + `move_guard` → die once the last consumer across Phases 6/7/9 is gone.
   - `mcp_server/_move.py` + `kms_move` → Phase 9.
   - `documents.upsert()` redesign + new-column population → Phase 7.
   - Config split / vault-root removal → Phases 6/7/9; daemon config → Phase 6.
3. **New columns ship nullable; readers must tolerate NULL.** Phase 9 `_resolve.py` tier-2 (`full_body`) degrades to tier-3 (vault path) when NULL. No backfill (clean slate — rearch doc §32/§61).

## Considered options

- **Big-bang rewrite (Phase 5 as written).** Redesign/split/retire everything up front. Rejected: breaks the repo and ~1370 tests at once, violates Phase 5's own "remaining tests pass" acceptance, and pulls Phase 6/7/9 work into the foundation pass with no isolation.
- **Accept a broken build between phases.** Delete modules now, let later phases catch up. Rejected: a non-importable repo blocks all parallel work (Phase 6 and 7 are meant to run in parallel) and makes every intermediate state untestable.
- **Additive, defer-to-consumer (chosen).** Each phase stays green; breaking changes are localized to the phase that owns the rewrite.

## Consequences

- **Phase 5 Slice 1 shrinks** to three additive deliverables: `knowledge_entries` table + 3 nullable `documents` columns (one migration), `storage/knowledge_entries.py` CRUD, and `config/dimensions.yaml` + `validate_dimension_tag()`. It touches no existing path and breaks no existing test.
- **`pipelines/reconcile.py` has no owning phase.** Rearch doc §11 says the 7-stage reconcile is replaced by daemon scan and "DB-only reconcile may survive in simplified form," but no phase owns the transition. It must be assigned before Phase 6, because reconcile is a live consumer of several dead-module targets. (Tracked as an open question.)
- **Per-phase test discipline:** each later phase deletes/rewrites only the tests tied to the consumer it is rewriting, keeping the suite green at every phase boundary.
- **Roadmap is annotated** with ⚠️ warnings on Phases 5/6/7/9 pointing to this contract so future phase-AIs do not re-attempt the up-front breaking changes.
