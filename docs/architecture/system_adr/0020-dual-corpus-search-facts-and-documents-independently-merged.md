# ADR-0020: Dual-corpus search — facts and documents searched independently then merged

**Status:** Proposed
**Date:** 2026-06-15
**Deciders:** Owner + Phase 9 grill session
**Context:** Phase 9 MCP Adaptation design

## Context

Phase 9 introduces a second searchable corpus alongside the existing document index: the `knowledge_entries` table (short extracted facts). The MCP `kms_search` tool must return both types of result. Two design options exist:

1. **Single-corpus**: Search only `knowledge_entries` (facts are the distilled memory; documents are just source material). Use doc references on facts to link back.
2. **Dual-corpus**: Search both `knowledge_entries` and `documents` independently, merge results, and identity-deduplicate.

## Decision

**Dual-corpus search.** `kms_search` runs two independent queries — one against the new fact hybrid index (`facts_fts` + `facts_vec`), one against the existing document search infrastructure (`notes_fts` + `embeddings_vec`) — merges them into one result set, and identity-deduplicates (same row id appears only once).

## Rationale

**Recall safety-net.** There is a window between when a document is captured (summary immediately searchable) and when the classify pipeline extracts facts from it (minutes to hours, depending on queue depth). During this window, a single-corpus search over facts would miss the document entirely. The document corpus catches it.

**Complementary retrieval signals.** Facts are short targeted insights ("Anthony leads the Q2 initiative"). Document summaries are longer structured digests covering multiple topics. A query may match one corpus but not the other. Showing both gives the consuming AI a more complete picture.

**Content overlap is acceptable.** A fact bullet and a document summary that describe the same real-world event are both returned. This is by design: facts provide the targeted answer; summaries provide the broader context. The consuming AI benefits from both perspectives.

**Identity-dedup is cheap.** The merge deduplicates by database row id (fact id or doc id), not by content similarity. This is an O(n) set operation — no expensive similarity computation.

## Consequences

**Positive:**
- No recall gap for freshly-captured, not-yet-classified documents.
- Richer results for the consuming AI (targeted facts + contextual summaries).
- Forward-compatible: if a third corpus is added later (e.g. corrections, synthesis), the merge pattern extends naturally.

**Negative:**
- Two queries per search instead of one. Acceptable latency cost: both queries run against local SQLite indexes.
- Merge logic is new code that must be tested for identity-dedup correctness and ordering stability.
- Response payloads are larger (facts + doc summaries). Mitigated by top-K caps on each corpus.

**Risks:**
- If the fact corpus grows very large, the merge could surface too many facts and crowd out document results. Mitigated by per-corpus caps (config-driven).

## Alternatives Considered

**Single-corpus (facts only):** Simpler, but creates a recall gap for unclassified documents and loses the broader context that document summaries provide. Rejected.

**Sequential (facts first, documents as fallback):** Only search documents if fact results are below a threshold. Adds a branching decision that is hard to tune and may miss relevant documents when facts are plentiful but cover different aspects of the query. Rejected.
