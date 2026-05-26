---
created: 2026-04-25
updated: 2026-05-05
---
```text
AI-kms/
├── config/
│   ├── config.yaml                 ← already exists
│   ├── thresholds.yaml             ← confidence cutoffs (auto/suggest/clueless)
│   └── routing.yaml                ← folder-routing rules per note type
│
├── prompts/                        ← Pattern: Prompts as Config
│   ├── classify.yaml
│   ├── summarize.yaml
│   ├── extract_metadata.yaml
│   ├── promote.yaml
│   ├── synthesize_weekly.yaml
│   ├── documentation_update.yaml
│   └── briefing.yaml
│
├── llm/                            ← already exists
│   ├── provider.py
│   ├── claude_provider.py
│   ├── ollama_provider.py
│   └── prompt_loader.py            ← NEW: loads prompts/*.yaml once at startup
│
├── core/                           ← shared primitives every module imports
│   ├── config.py                   ← loads + validates config/*.yaml at startup storage/
│   ├── result.py                   ← Pattern: Success / Failure result type
│   ├── audit.py                    ← Pattern: Audit Trail writer
│   ├── confidence.py               ← Pattern: Confidence-Gated Routing
│   ├── pipeline.py                 ← Pattern: Pipeline runner (compose stages)
│   ├── logging_setup.py
│   └── exceptions.py
│
├── handlers/                       ← Pattern: Handler Registry
│   ├── base.py                     ← Handler ABC: can_handle(), extract()
│   ├── registry.py                 ← self-registration + lookup
│   ├── pdf_handler.py
│   ├── email_handler.py
│   ├── youtube_handler.py
│   ├── web_article_handler.py
│   ├── chat_session_handler.py
│   ├── markdown_handler.py
│   └── docx_handler.py
│
├── pipelines/                      ← one file per roadmap feature
│   ├── capture.py                  ← Roadmap 1: extract → summarize → metadata
│   ├── classify.py                 ← Roadmap 2: classify → route → move
│   ├── search.py                   ← Roadmap 3 + 4: semantic + 3-tier retrieval
│   ├── synthesis_weekly.py         ← Roadmap 5
│   ├── documentation.py            ← Roadmap 6
│   ├── promotion.py                ← Roadmap 7
│   └── self_learning.py            ← Roadmap 8
│
├── vault/                          ← all filesystem I/O for the Obsidian vault
│   ├── paths.py                    ← resolves Vault/, inbox/, Projects/, etc.
│   ├── reader.py                   ← parse markdown + frontmatter
│   ├── writer.py                   ← idempotent upserts; respects updated_by_human
│   ├── indexer.py                  ← scan vault, detect changes by hash
│   └── frontmatter.py              ← YAML frontmatter helpers
│
├── storage/                        ← persistent state (not the vault)
│   ├── migrations/ ← NEW: versioned schema deltas (002_*.sql, 003_*.sql, ...)
│   ├── db.py                       ← SQLite connection + migrations
│   ├── schema.sql                  ← documents, embeddings, audit_log, corrections
│   ├── embeddings.py               ← write/read vectors
│   └── audit_log.py                ← append-only audit table queries
│
├── retrieval/                      ← Roadmap 3 + 4
│   ├── semantic.py                 ← embedding-based search
│   ├── keyword.py                  ← FTS5 search
│   ├── hybrid.py                   ← combine both
│   └── tiers.py                    ← hot / warm / cold dispatcher
│
├── briefings/                      ← Roadmap = the Briefings/ folder writer
│   ├── daily.py                    ← composes the daily briefing
│   └── classification_report.py    ← "what got moved where today"
│
├── mcp_server/                     ← Roadmap 9
│   ├── server.py                   ← MCP entrypoint
│   ├── tools.py                    ← tool definitions (search, classify, etc.)
│   └── transport.py                ← stdio / HTTP
│
├── cli/
│   ├── main.py                     ← `kms` entrypoint
│   ├── commands_capture.py
│   ├── commands_classify.py
│   ├── commands_briefing.py
│   └── commands_admin.py
│
├── scheduler/
│   ├── runner.py                   ← cron-like loop (or APScheduler wrapper)
│   └── jobs.yaml                   ← which pipeline runs when
│
├── tests/
│   ├── test_handlers/
│   ├── test_pipelines/
│   ├── test_vault/
│   └── fixtures/
│
├── data/                           ← gitignored: kb.db, embeddings cache, logs
├── logs/                           ← gitignored
├── pyproject.toml
├── README.md
└── test.py                         ← already exists
```
### Why each folder exists
**`config/`** — All tunable behavior lives here. Splitting `thresholds.yaml` and `routing.yaml` out of the main `config.yaml` lets your technical team adjust automation aggressiveness without touching provider settings.

**`prompts/`** — Edits to AI behavior happen here, never in code. One YAML per AI task, loaded once by `llm/prompt_loader.py`.

**`core/`** — The four cross-cutting patterns (Result, Audit, Confidence, Pipeline) live in one place so every module imports from the same source of truth. If you put `result.py` inside `pipelines/`, handlers can't use it cleanly.

**`handlers/`** — Adding a new input source = drop one file in `handlers/`, register in `registry.py`. Nothing else changes. This is the Handler Registry pattern verbatim.

**`pipelines/`** — Each file is one roadmap feature, written as a sequence of pure-function stage calls. Reading `capture.py` should read like a recipe: extract → summarize → classify → store.

**`vault/`** — All Obsidian filesystem I/O is isolated here. The rest of the codebase never touches `pathlib` or `open()` against the vault directly. This is what protects the `updated_by_human = true` rule — every write goes through `vault/writer.py`.

**`storage/`** — Database state (audit log, embeddings, document index) is _not_ the same thing as the vault. Keeping them separated mirrors the reference project's "Obsidian = source of truth, SQLite = retrieval layer" architecture.

**`retrieval/`** — The three-tier model (hot/warm/cold) is one concept, so it gets one folder. Pipelines call `retrieval.tiers.fetch(query, tier='hot')` — they don't know whether it came from FTS5 or embeddings.

**`briefings/`** — Composes the daily report. Reads from `storage/audit_log.py` and `retrieval/`, writes to `Vault/Briefings/`.

**`mcp_server/`** — Roadmap step 9 is a thin layer over the pipelines. Keeping it separate means the core system runs fine without it.

**`cli/`** — Human entry points. Each command file is small and just calls into a pipeline.

**`scheduler/`** — Defaults-and-invisibility layer. The user never runs anything manually; the scheduler triggers `capture` continuously, `briefing` daily, `synthesis` weekly.

### How the patterns map to the structure

| Pattern                  | Where it lives                                                                               |
| ------------------------ | -------------------------------------------------------------------------------------------- |
| Pipeline Pattern         | `core/pipeline.py` defines the runner; each `pipelines/*.py` composes stages                 |
| Confidence-Gated Routing | `core/confidence.py` reads `config/thresholds.yaml`; called by every pipeline before a write |
| Handler Registry         | `handlers/registry.py` + `handlers/base.py`; new sources = new file                          |
| Result Type              | `core/result.py`; every public function in pipelines/handlers returns `Success` or `Failure` |
| Prompts as Config        | `prompts/*.yaml` loaded once by `llm/prompt_loader.py` at startup                            |
| Audit Trail              | `core/audit.py` writes to `storage/audit_log.py`; `briefings/daily.py` reads from it         |
| Idempotent Writes        | `vault/writer.py` and `storage/db.py` — both upsert-only, both check `updated_by_human`      |