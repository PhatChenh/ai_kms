# Container Diagram — AI-kms All Phases
Scope: All functional blocks of the system, their build status, and how they
connect. Phase 0 is the foundation everything else depends on.

Status key:  ✅ complete   🔄 next up   ⬜ planned

Box standard: ~20 char wide, ~7 row high. Full descriptions in Diagram Notes below.

---

## Phase Map

```
  ACTORS              SYSTEM BOUNDARY                           EXTERNAL SERVICES
  ───────             ───────────────────────────────────       ─────────────────

  (Manager)      ─►  ┌──────────────────────┐
  (Tech Team)    ─►  │ CLI                  │                  ┌──────────────────┐
  (Claude        ─►  │ kms capture          │                  │ Anthropic        │
   Desktop)          │ kms watch            │                  │ Claude API       │
                     │ kms search           │                  │                  │
                     │ kms classify         │                  │ primary AI       │
                     │ kms reconcile        │                  │ for all tasks    │
                     │ kms briefing         │                  └────────┬─────────┘
                     └──────────┬───────────┘                           │
                                │ calls                                 │ AI calls
                                ▼                                       │
  ┌─────────────────────────────────────────────────────────────────────┼──────┐
  │  ┌──────────────────────┐                                           │      │
  │  │ Phase 4              │  ⬜  MCP Server MVP                        │      │
  │  │ MCP Server           │      thin wrapper over Phases 1–3         │      │
  │  │ ⬜ planned ~30 May   │      3 tools: search, capture, classify    │      │
  │  └──────────┬───────────┘                                           │      │
  │             │ calls pipelines                                        │      │
  │  ┌──────────▼───────────┐  ┌──────────────────────┐                 │      │
  │  │ Phase 2              │  │ Phase 3              │                 │      │
  │  │ Classify + Route     │  │ Search + Retrieval   │                 │      │
  │  │ 🔄 next up           │  │ ⬜ planned ~15 May   │                 │      │
  │  └──────────┬───────────┘  └──────────┬───────────┘                 │      │
  │             │                          │                             │      │
  │  ┌──────────▼──────────────────────────▼───────────────────────────►│      │
  │  │ Phase 1                                                           │      │
  │  │ Capture (+ Reconcile)                                             │      │
  │  │ ✅ complete                                                        │      │
  │  └──────────┬────────────────────────────────────────────────────────┘      │
  │             │ all phases import from                                         │
  │  ┌──────────▼───────────┐  ┌──────────────────────┐  ┌──────────────────────┐│
  │  │ Phase 0              │  │ Phases 5–7           │  │ Phases 8–9           ││
  │  │ Foundations          │  │ Promote / Docs /     │  │ Briefing /           ││
  │  │ ✅ complete          │  │ Self-learn           │  │ Synthesis            ││
  │  └──────────────────────┘  │ ⬜ planned June      │  │ ⬜ planned June      ││
  │                            └──────────────────────┘  └──────────────────────┘│
  └────────────────────────────────────────────────────────────────────────────────┘
              │                         │                          │
              ▼                         ▼                          ▼
  ┌──────────────────────┐  ┌──────────────────────┐  ┌──────────────────────┐
  │ Obsidian Vault       │  │ SQLite Database      │  │ Ollama               │
  │ (files on disk)      │  │ (local, alongside    │  │ (local AI,           │
  │                      │  │ code)                │  │ optional fallback)   │
  └──────────────────────┘  └──────────────────────┘  └──────────────────────┘
```

---

## Diagram Notes

| Block | What it does |
|---|---|
| **CLI** | Entry point for all manual commands. Each command calls exactly one pipeline and returns. No business logic. |
| **Phase 4 — MCP Server** | Thin wrapper exposing 3 tools (search, capture, classify) so Claude Desktop can talk to the vault. No logic lives here — tools just call pipelines. |
| **Phase 2 — Classify + Route** | Reads inbox notes, asks AI which project/domain each belongs to, confidence-gates the decision (auto/suggest/human), moves the note, writes audit entry. Also resolves CLUELESS binary markers left by Phase 1. |
| **Phase 3 — Search + Retrieval** | Makes vault queryable by meaning. Keyword (FTS5) + semantic (embeddings) + hybrid re-rank. Three-tier dispatcher: hot (summaries) → warm (snippets) → cold (full note). Callers never pick the tier — they provide a query and max cost. |
| **Phase 1 — Capture** | Watches inbox for dropped files. 6-stage pipeline: extract text → enrich URLs → AI summarize → AI label → apply location tags → write to vault. Handles .md, PDF, DOCX, XLSX. Includes `kms reconcile` (6 stages) to repair orphaned files. |
| **Phase 0 — Foundations** | Config, AI providers, audit log, document index, vault read/write. Never modified after it is built. Everything else imports from here. |
| **Phases 5–7** | Post-M2 features: Promote turns raw notes into structured knowledge. Documentation auto-updates project pages. Self-learning feeds your corrections back into the classify prompt. |
| **Phases 8–9** | Daily Briefing reads audit_log, produces a morning digest. Weekly Synthesis reads all week's notes, surfaces recurring themes and contradictions. Both write to the vault. |
| **Obsidian Vault** | A folder of markdown files on disk. AI-kms watches it for new drops and writes results back. The user reads from it via Obsidian's UI. |
| **SQLite Database** | Document index + full audit log + corrections table. Never stored inside the vault. Stays local. |
| **Anthropic Claude API** | Primary AI. Called for every summarization, classification, metadata extraction, promotion, documentation update, synthesis. |
| **Ollama** | Optional local AI. Used when cloud API is unavailable or for private content that must not leave the machine. |

---

## Delivery Milestones

```
  Phase 0   Phase 1   Ph 1.5    Pre-2     Phase 2   Phase 3   Phase 4   Phases 5-9
  ✅ done   ✅ done   ✅ done   ✅ done   🔄 next   ⬜        ⬜         ⬜
  │         │         │         │         │         │         │          │
  └─────────┴─────────┴────┬────┘         └─────────┤         │          │
                           │                        │         │          │
                      Shipped                  M1: capture+   │     M3: 30 June
                      (2026-06-03)             classify+search│   "full feature set"
                      797 tests                end-to-end     │
                                                         M2: MCP MVP
                                                         "boss demo"
```

---

## Supplementary: End-to-End Data Flow (complete product)

How a file travels from your desk to a searchable, filed note.

```
  You drop a file                         (anything: .md, PDF, DOCX, link)
          │
          ▼
  ┌──────────────────────┐
  │ Phase 1 — Capture    │
  │ extract text         │
  │ fetch linked pages   │  ← if URL-heavy
  │ AI: write summary    │
  │ AI: label + tag      │
  │ write to vault       │
  └──────────┬───────────┘
             │ note in inbox with AI summary + tags
             ▼
  ┌──────────────────────┐
  │ Phase 2 — Classify   │
  │ AI: which project?   │
  │ ≥85% → auto-move     │
  │ 60-85% → suggest     │  ← you review, one click
  │ <60% → human review  │  ← you decide
  └──────────┬───────────┘
             │ note filed in Projects/<A>/ or Domain/<D>/
             ▼
  ┌──────────────────────┐    ┌──────────────────────┐
  │ Phase 3 — Search     │    │ Phase 6 — Docs        │
  │ indexed for keyword  │    │ project doc updated  │
  │ and semantic search  │    │ automatically        │
  └──────────┬───────────┘    └──────────────────────┘
             │
             ▼
  ┌──────────────────────┐    ┌──────────────────────┐
  │ Phase 4 — MCP        │    │ Phase 8 — Briefing   │
  │ Claude Desktop can   │    │ appears in your      │
  │ find + discuss it    │    │ morning digest       │
  └──────────────────────┘    └──────────────────────┘
```
