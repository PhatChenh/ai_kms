# AI-KMS Domain Context

Key terms and concepts specific to this project. General programming terms omitted.

## Language

### Pipeline & Capture

**LOCATED:**
A non-md binary capture outcome where the file's vault path reveals project or domain context. Binary is moved to the appropriate `attachment/` folder; a rich sibling `.md` summary is written.
_Avoid_: "routed", "placed"

**CLUELESS:**
A non-md binary capture outcome where no project/domain context can be derived from path. Binary is parked in `inbox/`; a pending-routing marker `.md` is written for Phase 2 Classify to resolve.
_Avoid_: "unrouted", "unclassified"

**SKIPPED:**
A capture outcome where the file's content hash matches an existing `documents` row. Pipeline exits early — no LLM calls, no frontmatter overwrite. (Planned — not yet implemented; see behavior_adjustment.md § Idempotent.)
_Avoid_: "duplicate", "already captured"

**LOCATION_OVERRIDE:**
An audit entry written by the `apply_location_tags` stage when it adds or changes a `domain/<D>` tag or `project:` field based on file path, overriding or supplementing the AI-inferred value. Not required by C-13 (location is deterministic, not an AI decision) but useful for Phase 8 observability.
_Avoid_: "path correction", "location fix"

**location confidence:**
Certainty about domain/project tags derived purely from a file's vault path position — zero AI cost, deterministic. Distinct from AI confidence scores (which are probabilistic).
_Avoid_: "path-based tagging", "folder-inferred tag"

**apply_location_tags:**
A pipeline stage inserted between `metadata` and `store` in the capture pipeline. Inspects `raw.source_path`, adds `domain/<D>` to `tags` for domain-folder files, sets `project: <A>` for project-folder files. Does not call LLM.
_Avoid_: "location stage", "path tagger"

**_location_context(path, vault_cfg):**
A helper function in `vault/paths.py` that inspects a file path and returns `(location_type, location_name)` — e.g. `("domain", "Strategy")` or `("project", "Alpha")` or `(None, None)`. Extracted from inline logic in `_store_nonmd`. Shared by `apply_location_tags` (capture) and Phase 2 Classify.
_Avoid_: "path detector", "location resolver"

### Tags & Frontmatter

**domain tag:**
A `domain/<D>` string entry inside the `tags: list[str]` frontmatter field — Obsidian's special tag field. Multiple domain tags per note are allowed.
_Avoid_: `domain:` field (that is a separate legacy string field, not an Obsidian tag)

**project tag / project field:**
The `project: "<A>"` separate frontmatter string field. Not an Obsidian tag. One per note. Set by location only — AI does not infer it.
_Avoid_: "project tag in tags list"

**type tag:**
A `type/<name>` string entry in `tags` (e.g. `type/report`). Exactly one required per note. Validated against `config/tags.yaml::allowed_types`.

**free tag:**
A tag with no namespace prefix (e.g. `strategy`, `q1-review`). 5–10 required per note. Must not start with any `domain/`, `type/`, or other prefix.

### Phase 2 — Classify

**Project Registry:**
The shared, live in-memory lookup of all valid vault destinations — active projects (`Projects/<A>/`) and domain folders (`Domain/<D>/`) — that Classify, Search, and Briefing query. Populated at startup by scanning vault folder structure; kept live by the watcher (folder add/rename/archive triggers an update). Does not include archived projects (`Domain/<D>/Archive/`). Output is grouped by domain; projects with no or stale domain tag appear under `Uncategorized`.
_Avoid_: "project list", "destination table"

**valid destination:**
A project folder or domain folder that an inbox note may be filed into by the Classify pipeline. Specifically: any `Projects/<A>/` or `Domain/<D>/` entry in the Project Registry. Excludes `Domain/<D>/Archive/` entries and `inbox/` itself.
_Avoid_: "routing target", "output folder"

**inline classify:**
The new destination-classify step that runs INSIDE the single-file capture pipeline (not as a separate post-capture pipeline) for a note or binary that has no folder location — i.e. a loose inbox drop. Reuses the existing `classify()` pure function as its engine. Under the derive-from-tags routing model the step ASSIGNS the note's project field and designates a primary domain (consistent with the domain tags the metadata stage already produced); the destination folder is then derived, not freely picked. A file already inside a project/domain folder skips this step entirely (location wins). Distinct from folder classify, which already runs for whole-folder drops.
_Avoid_: "auto-classify", "classify pipeline" (the engine is a pure function; this is a capture stage)

**primary domain:**
When a loose note has no project but more than one domain tag, the single "home" domain the AI designates as the move target. The note still carries ALL its domain tags; only the move (the folder it lands in) uses the primary. With exactly one domain tag, that domain is the primary by default.
_Avoid_: "main tag", "default domain"

**derived routing (derive-from-tags):**
The rule that turns a note's assigned tags + project into a destination folder, instead of the AI naming a folder directly. Fixed precedence: project field present → move to `Projects/<project>/`; no project, has domain(s) → move to `Domain/<primary>/`; neither → CLUELESS (stays in inbox). Because the folder is computed from the stamped tags/project, the frontmatter and the on-disk location can never disagree (structural consistency).
_Avoid_: "free target pick", "AI picks the folder" (the previous, superseded model)

**classify outcome record:**
What the capture pipeline writes to a note's frontmatter when inline classify produces a SUGGEST or CLUELESS result. It captures the AI's candidate destination, confidence, and one-sentence reasoning so a later phase can surface it for human confirmation. The file stays in the inbox; nothing is moved. Recorded via a frontmatter field set (e.g. `status: needs-review` plus suggested-destination / reasoning) — the human confirm action itself is out of scope for this feature.
_Avoid_: "pending-routing marker" (that older binary-only concept retires; this record applies to notes and binaries and carries the AI candidate, not just a placeholder)

**Uncategorized (registry group):**
A catch-all group in the Project Registry output containing active projects whose `CLAUDE.md` has no domain tag, an unrecognised domain tag, or a stale domain tag (domain folder was deleted/renamed). Classify can still route to these projects; the AI prompt explains the gap and instructs semantic inference. Reconcile resolves the underlying CLAUDE.md issue.
_Avoid_: "unknown domain", "unassigned project"

### Phase 3 — Search / Retrieval

**hybrid search:**
The Phase 3 query strategy that combines two different ways of finding notes — a word/keyword match and a meaning/semantic match — and blends their rankings into one ordered list. Distinct from a single-index search; "hybrid" means both the keyword index (`notes_fts`) and the vector index (`embeddings_vec`) are consulted for the same query, then merged.
_Avoid_: "combined search", "dual search"

**Reciprocal Rank Fusion (RRF):**
The rule that merges two ranked lists (keyword ranking + meaning ranking) into one fused order using each item's *rank position*, not its raw score: an item's fused score is the sum of `1 / (k + rank)` across the lists it appears in (k is a smoothing constant, conventionally 60). Used so the two indexes' incomparable score scales (BM25 vs vector distance) never have to be normalised and added.
_Avoid_: "score averaging", "blended score"

**cross-encoder reranker:**
A small in-process model (ships inside `sentence-transformers` as `CrossEncoder`) that takes the top fused candidates and re-scores each one against the exact query text, producing the final ordering. Distinct from the embedding model: the embedder turns one text into a vector independently; the cross-encoder reads the query and a candidate *together* and outputs a relevance score. Runs locally; never an API call.
_Avoid_: "the reranker model" (be specific — it is a cross-encoder), "second LLM"

**metadata pre-filter:**
The first stage of `search()` — it narrows the full note set down to a candidate list using structured filters (project and/or date range) read from the `documents` table, *before* any ranking runs. When no filter is given, all notes are candidates. The hybrid ranker only ever searches within this candidate set.
_Avoid_: "the filter step", "pre-query"

**filter-only mode:**
The branch of `search()` taken when the caller supplies filters (project/date) but **no query term** — the candidates are sorted by most-recently-updated, capped, and returned with no ranking or reranking. Lets callers like weekly Synthesis ask "what landed in this project / this week?" without a search phrase.
_Avoid_: "no-query search", "listing mode"

**AI-triage payload:**
The cheap structured card `search()` returns per result — a note handle (`vault_path`), summary, snippet, score, and a metadata block — carrying **no full note body**. Designed so the Phase 4 MCP AI can scan many results cheaply, judge relevance, and pull full content via `read_note` only for the few notes it needs. The `metadata` field is load-bearing (the AI triages on it), not decoration.
_Avoid_: "search result object" (generic), "result row"

### Phase 4 — MCP / Context Injection

**context injection:**
The Phase 4 behaviour where the MCP server embeds the relevant `CLAUDE.md` / `context.yaml` background files *inside* a search or read tool response, always ordered before the results/content they explain, so the AI client is briefed about the user's domains and projects without having to call a separate tool. Distinct from a plain result list: the response is an ordered list of blocks (context first, content second). The amount injected is gated by result concentration (see frequency threshold / cap).
_Avoid_: "context prepend", "attaching context" (be specific — it is scoped + deduped, not a blanket attach)
_Source_: ADR-0010 (proposed)

**frequency threshold / cap:**
The two tunable numbers that decide how much background to attach. The *threshold* (default 0.3) is the share of search results a single domain/project must reach before its context file is injected — below it, the query is too broad and no context is sent. The *cap* (default 3) is the maximum number of context files in any one response. Both live in `config.yaml` under `mcp.context_injection`, never in code (C-06).
_Avoid_: "context limit", "30% rule" (name both the threshold and the cap)

**hash-dedup session state:**
The in-memory record the MCP server keeps of which context files it has already sent in the current conversation, keyed by each file's content fingerprint (a hash). If the same content was already sent, the server replaces it with a short "context for X already provided" note instead of resending the whole file. The record lives only for the life of the conversation's server process — a new chat starts with a clean slate. If a `CLAUDE.md` is edited mid-conversation its hash changes, so the new content is sent again (correct).
_Avoid_: "context cache", "dedup table" (be specific — it is per-conversation, in-memory, hash-keyed)

**two-tool progressive disclosure:**
The industry-standard split where the AI first calls a cheap search tool to scan many summary cards, then calls a separate read tool to pull the full content of only the few notes it actually needs — instead of loading every full note up front. In this project: `kms_search` returns cards, `kms_read` returns full bodies. Saves large amounts of context window on broad queries.
_Avoid_: "search-then-read" (acceptable shorthand, but name the pattern), "lazy loading"

**kms_inspect re-extraction:**
The behaviour of the `kms_inspect` MCP tool: when the AI needs the exact raw text of a binary source (a quote, a table) rather than the AI summary, the tool re-runs the original file's text extractor and returns the raw extracted text — no AI call, no new summary. It accepts either the sibling `.md` path or the binary path; given a sibling, it finds the binary via the sibling's `attachment_path` frontmatter field, then dispatches by extension through the handler registry.
_Avoid_: "re-summarize", "re-capture" (inspect does NOT summarize or capture — it only extracts and returns raw text)

### Vault Layout

**sibling `.md`:**
A markdown summary file created alongside a non-md binary, named `<binary.name>.md` (e.g. `report.pdf.md`), stored under `attachment/.summaries/`. The `documents` row for the binary points to this sibling as `vault_path`.
_Avoid_: "shadow file", "proxy note"

**CLUELESS marker:**
The specific sibling `.md` written for a CLUELESS binary — parked at `inbox/.summaries/<filename>.md` with `status: pending-routing`. Body is a one-line placeholder. Phase 2 Classify overwrites body and routes binary.
_Avoid_: "pending note", "inbox marker"

**no-edit file:**
A non-`.md` file whose extension is in `VaultConfig.no_edit_extensions` (default: pdf, png, jpg, jpeg, gif, webp). Routed to the hidden `attachment/` folder — not visible to the user in Obsidian. Contrast with editable file. Routing via `resolve_placement()` in `vault/paths.py`.
_Source_: ADR-0006 (accepted 2026-06-04)

**editable file:**
Any non-`.md` file NOT in `no_edit_extensions` (e.g. docx, xlsx, pptx). Lives in the project/domain root so the non-technical user can see and open it in place. NOT hidden in `attachment/`. Content changes detected by watcher and trigger re-summarization.
_Source_: ADR-0006 (accepted 2026-06-04)

**AI-output folder:**
One of `Briefings/`, `Synthesis/`, `Documentation/`. The AI writes here; users never drop source material here. Capture-excluded: watcher and scan_capture skip them entirely so AI outputs are never re-captured.
_Source_: ADR-0006 (accepted 2026-06-04)

**misplaced location:**
Any folder that is NOT one of {`inbox/`, a specific `Projects/<A>/`(+its `attachment/`), a specific `Domain/<D>/`(+its `attachment/`)} and is NOT an AI-output folder. Examples: bare `Projects/`, bare `Domain/`, `Domain/<D>/Archive/`, vault root. A file dropped in a misplaced location is swept to `inbox/` by the watcher.
_Source_: ADR-0006 (accepted 2026-06-04)

**batch-worthy subfolder:**
A folder whose location in the vault signals that a group of files belong together — specifically, any subfolder *inside* `inbox/`, `Projects/<A>/`, or `Domain/<D>/`, but NOT the root of those trees (`inbox/` itself, `Projects/<A>/` itself, or `Domain/<D>/` itself). Files captured from a batch-worthy subfolder are associated with a shared batch identifier so Phase 8 Briefing can group them. A file captured directly into `inbox/` root is NOT batch-worthy (no grouping signal).
_Avoid_: "subfolder drop", "grouped folder"

**live batch membership:**
The meaning of the `batch_id` foreign key on a `documents` row — it records which active batch the file *currently* belongs to based on its folder position, not when it was first captured. If a file moves from one subfolder to another, its batch membership updates. Distinct from a capture timestamp (which records when the file was first processed, never updates).
_Avoid_: "capture batch", "original batch"

**folder_path (on batches table):**
The vault-relative POSIX path of the subfolder that triggered the batch creation (e.g. `inbox/Q2-reports`). Used to look up whether a batch already exists for a given subfolder before creating a new one. NOT UNIQUE — multiple batch rows for the same folder_path are valid (e.g. re-drops after a cleanup); lookup always picks the most recent row.
_Avoid_: "batch folder", "source folder"

### Cloud-Native — Knowledge Entries

> Introduced by the cloud-native rearchitecture (`docs/0_draft/cloud_native_rearchitecture.md` §7). These concepts replace the per-project `CLAUDE.md` files as the system's living context. The living context now lives in a database table, not in vault files.

**knowledge entry:**
One atomic fact about one thing, stored as a single row in the `knowledge_entries` database table. It records which dimension and entity the fact is about, which tag (sub-category) it falls under, the fact itself in plain words, how sure the system is, which documents it came from, and a confident/pending/retired status. Many small entries accumulate into the system's distilled memory of "what we know."
_Avoid_: "knowledge record", "memory note", "card"

**dimension:**
A top-level category of knowledge — for example people, projects, or domains. The full list of dimensions is fixed in a config file (`config/dimensions.yaml`); the AI may not invent new ones. Adding a dimension is a config + prompt change, never a database change.
_Avoid_: "category" (too generic), "table" (it is NOT a separate table — all dimensions share one table)

**entity:**
The specific thing a knowledge entry is about, stored as free text — for example "Anthony", "Movie Q2", or "Finance". Whether "Anthony" and "Anthony Nguyen" are the same entity (entity resolution / name normalization) is explicitly OUT of scope for Slice 1 and deferred to a later phase.
_Avoid_: "subject", "topic"

**tag (knowledge entry):**
A sub-category within a dimension that classifies a fact — for example `role` or `deadline`. Each dimension has its own fixed set of allowed tags in config, and every tag set MUST include a mandatory `other` catch-all for facts that fit no existing tag. Distinct from an Obsidian frontmatter "type tag" or "free tag" (those tag notes; this tags facts).
_Avoid_: conflating with the frontmatter `type/` or `domain/` tags used in capture

**fact:**
The atomic piece of knowledge a knowledge entry carries, written in plain words — for example "Product Lead for Movie Q2". The discipline is the same as the old `CLAUDE.md` files: short, crucial context only, never a dumping ground.
_Avoid_: "claim", "statement", "value"

**confident / pending / retired (entry lifecycle):**
The three states a knowledge entry's status can hold. **Confident** = a high-confidence extraction treated as current truth. **Pending** = medium/low confidence (hedging, speculation, secondhand) surfaced for human confirmation; can be promoted to confident as evidence accumulates. **Retired** = superseded by newer information; kept in the table for history with a reason, never deleted. Confidence drives the initial status, gated by thresholds in config.
_Avoid_: "active/inactive", "verified/unverified", "archived" (retired rows are not archived elsewhere — they stay in the same table)

**sources (on a knowledge entry):**
The list of documents a fact was extracted from, stored inside the knowledge entry row itself as a list of document references. Every entry MUST have at least one source — no fact without traceability back to where it was learned. As of Phase 8 these references are document IDs (stable integers), not vault paths, so they survive file moves without rewriting.
_Avoid_: "citations", "links", "provenance" (acceptable but plainer is preferred)

### Cloud-Native — Classify Infrastructure (Phase 8 Slice A)

> Introduced by Phase 8 Slice A (`docs/1_design/phase8_sliceA_classify_infra.md`). Slice A is the plumbing that finds work, prepares inputs, and runs a queue — the actual AI fact-extraction is Slice B. These terms describe how a document gets queued and prepared for classification.

**guidance (field on a dimension):**
A block of plain-English prompt text the user can write for each dimension, telling the AI what to look for in that dimension (e.g. "track who leads what, who reports to whom"). Stored alongside the dimension's allowed tags in `config/dimensions.yaml`. It is the user-injectable steering seam for extraction quality — present from Phase 8 onward, fed to the AI in Slice B.
_Avoid_: "instructions", "prompt" (it is one piece of a larger prompt, not the whole prompt)

**classify-fingerprint (`classify_content_hash`):**
A per-document marker recording the content fingerprint the document had the last time it was successfully classified. Empty means never classified; differing from the document's current content fingerprint means the content changed and needs re-classifying; matching means the work is already done and is skipped. This is how classify finds its own work with a single query — no separate queue table or flag.
_Avoid_: "classify flag", "dirty bit" (it is a hash comparison, not a boolean)

**work discovery:**
The single database query that finds documents needing classification — those whose classify-fingerprint is empty or no longer matches their content fingerprint. Run on container startup (catch-up scan) and conceptually each time capture finishes a document.
_Avoid_: "scan" alone (ambiguous with the daemon's vault scan)

**trust_score:**
A separate quality axis on each knowledge entry measuring user-validated quality, distinct from the AI's own confidence. Ships in Phase 8 as an inert flat default (0.5) and is only moved by user corrections in a later phase; Phase 8 uses it as the top sort key when ranking which existing facts to show the AI.
_Avoid_: conflating with "confidence" (confidence = AI certainty; trust = user validation — two independent loops)

**retrieval_score (decaying):**
A per-entry demand signal measuring how often a knowledge entry has been surfaced to the user-facing AI recently. On each injection (the entry appears in an MCP tool response): `score = score * decay_factor + 1.0`. A periodic sweep multiplies all scores by `decay_factor` to let old popularity fade. Prevents rich-get-richer lock-in where an entry surfaced early by luck dominates forever. Reinterprets the existing `retrieval_count` INT column (shipped inert in Phase 8, default 0) as a REAL — SQLite is type-flexible so no column rename is needed.
_Avoid_: "view count", "hits", "popularity" (it decays — not a simple counter)

**content reader:**
The Slice A step that reads a document's stored text and chooses what to feed the classifier: the full extracted text when it fits under a configured token budget, otherwise the shorter summary. A pure size-based switch — no AI involved.
_Avoid_: "extractor" (that is the daemon's text-extraction step, a different thing)

**context loader / fact ranker:**
The Slice A step that gathers the existing facts already known for a dimension (confident and pending only, never retired), ranks them by trust then confidence then recency, and keeps only the top N per dimension (a configured budget cap) so the prompt does not bloat. Each surviving fact carries its database ID so a later step can reference it for update or retirement.
_Avoid_: "memory loader", "history loader"

### Cloud-Native — Classify Extraction (Phase 8 Slice B)

> Introduced by Phase 8 Slice B (`docs/1_design/phase8_sliceB_extraction.md`; ADR-0018, ADR-0019). Slice B is the AI brain that Slice A's plumbing feeds: it extracts structured facts per dimension and safely writes them. These terms describe extraction, safe writing, and the self-correcting retry loop.

**entity extractor:**
The Slice B step that makes one focused AI call per dimension — feeding that dimension's guidance, its top-ranked existing facts (each with its database id), the document text, and any previous-attempt feedback — and parses the structured-JSON reply into a list of proposed facts to add, update, or retire. One call per dimension, never one monolithic call.
_Avoid_: "the classifier", "extraction model" (it is a step that calls a model, not the model itself)

**entry writer:**
The Slice B step that applies the extracted facts to the knowledge table: it routes each proposed fact by action (new → insert, update → edit the referenced fact, retire → retire the referenced fact), validates that referenced ids exist, accumulates sources on update, and folds exact duplicates. The only place that writes knowledge facts during classify.
_Avoid_: "fact saver", "writer" alone (ambiguous with the vault writer)

**source accumulation:**
The rule that when a new document updates an existing fact, the document's id is *added* to that fact's source list (and deduped), never overwriting the prior sources. Provenance must grow — "which documents back this fact" is the point — and the database does not merge for you, so the entry writer reads the existing list, appends, dedupes, and writes the merged list.
_Avoid_: "source overwrite", "re-sourcing" (the whole point is it accumulates, never replaces)

**exact-entity dedup backstop:**
The deterministic safety check the entry writer runs before inserting any brand-new fact: it looks up the database for an existing non-retired fact with the same dimension, entity, and tag, and if found, folds the new fact into that one (as an update) instead of spawning a twin. It exists because the AI only sees the capped top-N existing facts, so a duplicate ranked outside that window would otherwise slip through. Catches exact-entity duplicates only; name-variant duplicates ("Anthony" vs "Anthony Nguyen") are entity resolution, deferred.
_Avoid_: "unique index", "dedup constraint" (there is no DB uniqueness rule — it is a write-time lookup)

**self-correction retry (withhold-stamp loop):**
The behavior where a document that produced any bad fact or a failed audit write is deliberately *not* marked classified, so the work-discovery scan re-finds it and tries again — and on each retry the prompt carries a machine-generated note of what failed last time so the AI can correct itself. The good facts from each attempt are kept; duplicates fold away on retry. Distinct from a blind retry (which repeats the same mistake) and from the banned Phase-10 user-corrections slot (this feedback is machine-generated validation errors, not human edits).
_Avoid_: "retry queue", "redo" (there is no separate queue — it is the absence of the done-stamp plus the catch-up scan)

**parked for human review:**
The terminal state a document reaches after a configured number of failed classify attempts: auto-retrying stops, the document's status is set to needs-review (the work scan skips it), and an audit entry records why it was parked. It prevents a permanently-malformed AI reply from looping forever and burning tokens, while never losing the partial facts already written. A future web UI surfaces parked documents.
_Avoid_: "failed", "dead-lettered" (it is paused for a human, not discarded; the facts and the document survive)

**live enqueue:**
The wiring that puts a freshly-captured document's id straight onto the running work queue the moment capture finishes, so it classifies immediately instead of waiting for the next container restart. The queue is reached via the running web app's shared state; when no queue is present (command-line or test runs) the push is skipped and the startup catch-up scan remains the safety net.
_Avoid_: "push notification", "trigger" alone (be specific — it is an in-process queue put, gated on the queue being present)

### Cloud-Native — Deployment Foundation (P5 Slice 2)

> Introduced by P5 Slice 2 (`docs/1_design/P5_slice2_deployment_foundation.md`). These concepts let the existing cloud code run as a container on AgentBase and give the future daemon (Phase 6) a cloud endpoint to send files to. Purely additive infrastructure — no pipeline changes.

**cloud entry point:**
The container-mode startup module that boots the system as a web server instead of as a local command-line tool. It loads the existing knowledge-assistant (MCP) interface, attaches the new web endpoints (`/health`, `/api/upload`, `/api/event`), and runs them all on one network port (8080). The existing local "stdio" startup (for Claude Desktop on the user's laptop) is left untouched and keeps working. Distinct from the MCP server module itself — the cloud entry point wraps and serves it; it does not replace it.
_Avoid_: "main entry", "server" (be specific — it is the HTTP/container entry, separate from the stdio entry)

**/health endpoint:**
A tiny open web address the cloud platform pings to confirm the container is alive and ready. Returns a 200 "ok" with no secret-key check. AgentBase marks the container ACTIVE only once this responds; a failing or slow health check leaves the container stuck. Distinct from the sync endpoints — `/health` is the only path with no authentication.
_Avoid_: "ping", "status page"

**/api/upload (upload contract):**
The web endpoint the future daemon calls to send one file's already-extracted text plus its details (location, original filename, size, a content fingerprint, and an open-ended metadata block) into the cloud database. In Slice 2 it is a deliberate STUB: it only stores or updates the document record — no summarizing, no search indexing, no fact extraction (those come in Phase 7). It is idempotent by content fingerprint: same location + same fingerprint = do nothing; same location + different fingerprint = overwrite. Protected by a shared secret key.
_Avoid_: "capture endpoint" (it does NOT run the capture pipeline in Slice 2), "ingest API"

**/api/event (event contract):**
The web endpoint the future daemon calls to report that the user moved/renamed or deleted a file, so the cloud database can keep file locations in sync. A move updates the stored location (carrying search-index entries along); a delete removes the record and its search entries outright (hard delete). Naming a file the system never captured returns a plain "not found", not an error. One event type ("moved") covers both moves and renames. Protected by the same shared secret key as upload.
_Avoid_: "sync API" (too broad), "webhook"

**content fingerprint (content_hash, as idempotency key):**
A short hash of a file's content used by `/api/upload` to decide whether an incoming file is new, unchanged, or changed — without needing a separate request ID. Same location + same fingerprint means "already have it, skip"; a different fingerprint for the same location means "content changed, overwrite". Network retries that resend an identical file naturally de-duplicate.
_Avoid_: "checksum" (acceptable, but tie it to the idempotency role), "etag"

**Litestream:**
A small background helper that runs inside the cloud container next to the main app and continuously copies every database change up to cloud file storage roughly once per second. On container restart it downloads the latest copy back before the app starts, so the database survives even though the container itself keeps no permanent disk. The app reads and writes its database normally and is unaware Litestream exists. A crash can lose up to about one second of the most recent writes — an accepted trade-off at the current scale (3-4 testers).
_Avoid_: "backup script", "database" (Litestream is NOT a database — it only mirrors the SQLite file)

**(VNG) Object Storage:**
Cloud file storage (an S3-compatible "USB drive in the cloud") that holds the database backup files Litestream produces. It stores files; it cannot run database queries itself. The container has no permanent disk of its own, so the database must live here between restarts. Access is configured via environment-variable credentials, never baked into the container image.
_Avoid_: "the cloud", "bucket" (acceptable shorthand once introduced), "database store"

**dummy vault path (TD-059):**
A throwaway empty folder the container creates only to satisfy a current start-up validation that still insists a "vault root" folder exists on disk — even though no file-watching or vault code runs in the cloud. A stopgap until the configuration split (Phases 6/7/9) removes the vault-root requirement from the cloud side entirely. See TD-059. (Note: how the path is actually supplied to the running cloud process is an open question — see the Slice 2 design doc.)
_Avoid_: "vault" (there is no real vault cloud-side — it is an empty placeholder)

**blob:**
The raw bytes of a user's binary/visual file (a photo, screenshot, chart, scanned page) — the original file itself, as opposed to any text pulled out of it. Stored in cloud object storage, never inside the database. The database holds only a small pointer (the blob reference) to where the blob lives. Introduced in Phase 7B for the visual capture path.
_Avoid_: "attachment" (dead — the old vault `attachment/` folder concept is retired), "document" (the document is the DB row; the blob is the bytes it points to)

**blob store:**
The application's own write path into cloud object storage for blobs — a small in-house helper wrapping an S3-compatible client, with one put/get interface so the rest of the pipeline never speaks S3 directly. Distinct from Litestream, which also uses object storage but only mirrors the database file and cannot store arbitrary blobs. New in Phase 7B (the client is a new external dependency).
_Avoid_: "Litestream" (Litestream mirrors the DB; the blob store writes user files), "bucket" (the bucket is the storage; the blob store is the app's client to it)

**blob reference:**
The small pointer stored on a `documents` row that says where a file's blob lives in object storage (the object key), plus its file type (mime). Never the bytes — only the locator. NULL on text rows; populated only on binary/visual rows. Added by migration 009 in Phase 7B.
_Avoid_: "the file", "the blob" (the reference points AT the blob; it is not the blob)

**content-addressed (blob) key:**
The scheme where a blob's object-storage filename is derived from its content fingerprint, so identical bytes map to one stored object and moving a file (which changes its path) never orphans or re-uploads the blob. Distinct from row identity: each vault path still gets its own `documents` row and its own description, while the bytes are de-duplicated at the object level. Two rows with identical bytes therefore share one blob — which is why deleting a blob is reference-counted (delete only when the last row pointing at it is gone).
_Avoid_: "path-based key" (the rejected alternative — would re-upload on every move), "content-reuse-by-hash" (the rejected summary-sharing optimization — content-addressing the blob does NOT share descriptions across rows)

**Vision Describer:**
The picture-reading mode of the Housekeeping AI: for an image or text-less PDF it calls an image-capable model once to produce a few sentences describing what the picture shows (figures, labels, chart content), and that description becomes the file's searchable text. A second mode of the same summarizer stage, not a separate pipeline. The text path never invokes it. New in Phase 7B.
_Avoid_: "OCR" (it describes meaning, not just transcribes characters), "summarizer" (that is the text mode; the Vision Describer is the image mode of the same stage)

**store-blob-first:**
The Phase 7B extension of store-raw-first (ADR-0014): the raw bytes are written to the blob store and a blob reference is saved on the document row BEFORE the vision model runs. The user's file is safe the moment it is stored; the description is enrichment that may arrive late (or never, if the file is too big, unsupported, or vision fails). Mirrors the text path's "save text first, summarize second."
_Avoid_: "upload-then-describe", "save-and-summarize" (those blur the safety ordering that is the whole point)

**send-to-vision set (describable types):**
The config-driven list of file types that are routed to the Vision Describer — images (`image/*`) and text-less PDFs. Everything else (zip, video, spreadsheet-with-no-text) is stored as a blob but left undescribed. The set lives in config so new describable formats can be added without code changes (C-06 spirit). Whether the configured vision model can actually read a PDF (vs only images) is an open question (OQ-7G).
_Avoid_: "vision whitelist", "image types" (text-less PDFs are in the set too)

**needs-description (derived):**
The state of a binary/visual row whose description is empty — derived from the same empty-summary signal as 7A's "needs-summary" (`summary IS NULL`), NOT a new flag column. A row reaches this state three ways: the file was too big, the type was unsupported, or the vision call failed. The *reason* lives only in the audit log (a future UI surfaces it — TD-060).
_Avoid_: "undescribed flag", "pending-description column" (there is no column — it is derived)

**reference-counted delete:**
The blob-deletion rule: when a document row that references a blob is deleted, the blob in object storage is removed ONLY if no other document row still references the same object key. Because blobs are content-addressed, two rows with identical bytes share one blob, so deleting one row must not break the survivor. The document row is always deleted (hard delete, unchanged from 7A); only the blob's removal is conditional.
_Avoid_: "garbage collection" (it is a synchronous count-then-delete at delete time, not a sweep), "cascade delete" (the blob is shared, so a plain cascade would corrupt the survivor)

### Phase 9 — MCP Adaptation

> Introduced by Phase 9 (`docs/1_design/phase9_mcp_adaptation.md`; grill at `docs/0_draft/P9_mcp_adaptation_grill.md`). Phase 9 rewrites the MCP server from vault-disk-reading to cloud-database-reading, replaces CLAUDE.md context injection with knowledge-entry fact injection, and changes the tool surface.

**query facts:**
`knowledge_entries` rows whose text matches the user's search query, found by hybrid search (semantic + keyword) on the new fact search index. A selection-time role, not a stored attribute — the same entry is a query fact in one search and an orientation fact in another.
_Avoid_: "search results" (too generic — query facts are one tier of search results), "matching entries"

**orientation facts:**
Top-ranked `knowledge_entries` rows selected per dimension by the 4-key ranker (trust_score, retrieval_score, confidence, recency) to orient the consuming AI about the entities in scope, independent of any query text. Injected into `kms_vault_info` and `kms_search` responses.
_Avoid_: "background facts" (acceptable shorthand but less precise), "context" (overloaded)

**identity-dedup:**
The rule that the same database row (fact row id or document row id) appears at most once in any MCP tool response. Distinct from content dedup: a fact bullet and a document summary that describe the same real-world event are both kept — they serve different purposes (targeted insight vs general digest). Applied per response and also conversation-level (orientation facts already injected earlier are not re-injected).
_Avoid_: "content dedup", "hash dedup" (the old Phase 4 model — Phase 9 dedupes by row id, not by content hash)

**fact hybrid index:**
Two new search tables (`facts_fts` + `facts_vec`) that make the short fact text in `knowledge_entries` searchable by both keyword (BM25) and meaning (KNN embedding), using the same Reciprocal Rank Fusion technique as document search. Research gate: short one-liner facts may not separate well in embedding space — if separation is poor, the config-driven blend weight leans on keyword matching.
_Avoid_: "fact search" (too vague — specify it is a hybrid index with two components)

**three-tier resolve:**
The three levels of detail the `kms_inspect` tool can return for any document, accessed by integer document id: **summary** (the structured 5-section digest, always available from DB), **text** (the full extracted raw text from `documents.full_body`, may be NULL for old rows or binaries without text — degrades to summary), **file** (the vault-relative path, laptop-dependent — the consuming AI handles availability). Replaces the old disk-based binary resolver.
_Avoid_: "resolve modes", "inspect tiers" (use the canonical term "three-tier resolve")

**dual-corpus search:**
The `kms_search` strategy that runs two independent queries — one against `knowledge_entries` (fact hybrid index) and one against `documents` (existing `notes_fts` + `embeddings_vec`) — merges the results, and identity-dedupes. The document search is the recall safety-net: a freshly captured document is searchable by summary before the classify pipeline has extracted facts from it. ADR-0020.
_Avoid_: "two-phase search" (it is not sequential — both corpora are queried in parallel), "combined search"

**budget-capped injection:**
The cap mechanism that limits how many orientation facts and entity names are injected into any MCP tool response. Controlled by two config knobs: `max_entities_per_dimension` (how many entity names in the structural map) and `max_orientation_facts_per_dimension` (how many fact bullets per dimension). No global token budget — the two per-dimension caps are sufficient for Phase 9.
_Avoid_: "token limit", "context window management" (those are consumer-side concerns — this is server-side output capping)

### Phase 6 — Daemon Cache & Reconcile (Slice A2)

> Introduced by P6 Slice A2 (`docs/1_design/phase6/P6_slice_A2_cache_reconcile.md`; ADR-0013). The daemon gains a small local "notebook" as a speed layer. The cloud database stays the single source of truth; losing the notebook is never fatal.

**daemon cache (notebook):**
The daemon's local manifest mapping each note's vault location to its raw-byte content fingerprint (`vault_path → content_hash`), stored on the user's Mac. It is an **advisory** speed layer only — it lets the daemon skip unchanged files instantly and recognise moves — and the cloud database is always authority. Cache loss or corruption is non-fatal: a missing/damaged cache degrades to a full reconcile against the cloud (the stateless A1 behaviour), never data loss.
_Avoid_: "local database", "sync state" (it is not authoritative; it is a disposable hint), "index" (the cloud `documents` table is the index)

**cache-on-ack:**
The invariant that a file's fingerprint is written into the daemon cache ONLY after the cloud returns a success response from `/api/upload` or `/api/event`. A failed or timed-out upload leaves the file uncached, so the next reconcile re-detects and re-sends it. There is no persistent retry queue — reconcile is the durable net.
_Avoid_: "write-through cache", "optimistic cache" (the whole point is that it is NOT optimistic — never cache before the ack)

**3-way compare (reconcile):**
The daemon's startup and periodic reconcile that compares three facts per file — present on disk, present in the cache, present in the cloud (`GET /api/state`) — and resolves every disagreement with the cloud as authority. Upgrades A1's two-way disk↔cloud scan. It uploads new/changed files, heals cloud rollbacks (disk-present + cloud-absent → re-upload), reports confirmed offline deletions, and rebuilds the cache to match post-reconcile cloud truth.
_Avoid_: "diff", "sync" (too generic — name it as the three-input compare), "two-way scan" (that was A1)

**discard & rebuild:**
The rule for a damaged or unreadable daemon cache: throw the entire notebook away and rebuild it from cloud truth — never repair, never partially trust a salvaged entry (grill D2). A full rebuild is cheap (re-read local files + one cloud request); partial salvage is how silent drift creeps in.
_Avoid_: "cache recovery", "repair" (those imply salvaging the old contents — the notebook is disposable, not repairable)

**move-correlation window:**
The short wait (default 2s) after a file delete during which a re-created file with the **same content fingerprint** turns the delete+create pair into a single `moved` event instead of a destructive delete-then-recapture. Needs the cache because the deleted file is gone from disk — the cache holds the only surviving copy of its fingerprint. Biases toward catching moves at the cost of slightly delayed deletes (grill D1).
_Avoid_: "move timer", "debounce" (debounce coalesces repeated events on one path; this correlates two different paths by content)

**conservative delete (sweep):**
The rule that a cloud-known file found missing during a single background sweep is NOT reported deleted immediately — it must stay gone across more than one sweep (default 2 confirmations) before the daemon scrubs the cloud row (grill D3). A transiently-missing file that reappears is never deleted. Distinct from a live delete, which clears within the move-correlation window.
_Avoid_: "delayed delete", "soft delete" (the row is hard-deleted once confirmed; only the *decision* is delayed)

### Phase 10 — Self-Learning & Reports

> Introduced by Phase 10 (`docs/1_design/phase10_self_learning.md`; grill at `docs/0_draft/phase10/phase10_self_learning_grill.md`). Phase 10 closes the feedback loop: users can correct facts, and their corrections adjust trust, teach the AI, and generate health reports.

**fact correction:**
A record of a user saying a knowledge entry is right (confirm), wrong (reject), or needs fixing (revise). Stored in the `fact_corrections` table. One correction per row. Each records the operation, the reason category, optional user feedback, and snapshots of the old and new fact text and trust scores.
_Avoid_: "learning signal" (broader concept — fact corrections are one specific signal type), "feedback" alone (too generic)

**reason category:**
A tag on a correction distinguishing WHY the fact was wrong. `ai_error` = the AI misread the source document (this teaches the AI via few-shot injection). `stale_source` = the source document was outdated or incorrect, but the AI extracted faithfully (NOT fed to few-shot — would teach the AI to ignore documents).
_Avoid_: "error type", "correction type" (those describe the operation; reason category describes the root cause)

**overwrite guard:**
The rule that protects user-validated knowledge entries from being silently replaced by the classify pipeline. When an entry's `trust_score` exceeds a config threshold (default 0.5), `_should_overwrite()` in `classify_writer.py` returns `False`, and the pipeline writes a new `pending` entry instead of overwriting the trusted one. Both entries coexist, creating a detectable conflict.
_Avoid_: "write lock", "trust gate" (the guard does not lock — it redirects the write to a new row)

**few-shot injector:**
A component that selects past `ai_error` corrections, formats them as bad-fact/good-fact example pairs, and injects them into the `entity_extract.yaml` prompt during classify runs. Selection is relevance-first: dimension match, entity overlap with the current document, then recency. Capped by config (`max_corrections_per_prompt`).
_Avoid_: "training data", "fine-tuning" (it is in-context learning via prompt examples, not model training)

**volatility flag:**
An annotation appended to a knowledge entry's fact bullet in MCP context injection blocks when the entry has been corrected more than N times (config-driven, default 3). Signals to the consuming AI that the fact is contested or unstable.
_Avoid_: "disputed fact", "unstable flag" (volatility is the measured signal; the interpretation is up to the consumer)

**trust-floor filtering:**
The rule that knowledge entries with `trust_score` below a config threshold (default 0.3) are excluded from MCP context injection orientation blocks. They remain searchable and visible in `kms_inspect` — they are just not proactively surfaced.
_Avoid_: "hidden entries", "filtered out" (they are not hidden — they are deprioritized from proactive surfacing)

**entry comment:**
An additive human annotation on a knowledge entry, stored in `entry_comments`. No edit, no delete — comments only grow. Fed to the classify context loader so the AI considers human annotations during future extraction runs. Added via the `kms_comment` MCP tool.
_Avoid_: "note" (overloaded with vault notes), "annotation" (acceptable but less specific)

**report (knowledge report):**
A synthesized summary of knowledge system health, generated on demand by the housekeeping AI. Defined by a YAML config block specifying filters (which data to gather) and a prompt (what to synthesize). Stored in the `reports` table with full provenance (prompt used, filters applied, source IDs). Adding a new report type is a config-only change.
_Avoid_: "briefing" (that is a different Phase 8 concept), "dashboard" (there is no UI — reports are text)
