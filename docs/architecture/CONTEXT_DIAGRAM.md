# System Context — AI Knowledge Manager (AI-kms)
Scope: Why this system exists, who it serves, and how the complete product
addresses each pain point. Includes worked use-case scenarios for every major feature.

---

## Part 1 — The Problem This System Solves

```
  A manager's typical week WITHOUT AI-kms
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  MONDAY          WEDNESDAY        FRIDAY            3 WEEKS LATER
  ───────         ──────────       ──────            ─────────────
  Meeting ends    Report arrives   Research done     "Where is that
  → scribble      → skimmed once   → interesting     thing I noted
    quick notes     → "read later"   idea noted        about X?"
    → forgot to     → forgotten      → never            → search fails
    file                             revisited          → start over
       │                │                │                  │
       ▼                ▼                ▼                  ▼
  [inbox grows]   [downloads folder]  [sticky note    [context lost,
  [unfiled]       [200 PDFs]          buried in app]  repeat work]

  The five root causes:
  ┌──────────────────────────────────────────────────────────────────────┐
  │  ① ACCUMULATION  — files pile up faster than any human can organize  │
  │  ② NO TIME       — filing + tagging = 3+ hours of admin per week     │
  │  ③ TRANSCRIPTION — every meeting, report, article needs manual        │
  │                    reading and summarizing before it is useful        │
  │  ④ LOST IDEAS    — good insights from months ago: invisible forever   │
  │  ⑤ LOST TRACK OF TASK — so many on going projects with lots of todo   │
  └──────────────────────────────────────────────────────────────────────┘
```

---

## Part 2 — System Context Overview

The AI handles ① ② ③. The Briefing + Synthesis handles ④, ⑤.
You only make judgment calls the AI is uncertain about.

```
                      ┌──────────────────────┐
  (Manager)           │ AI-kms               │
  drops files ───────►│                      │
  reads results       │  Drop a file.        │─────────────► (Anthropic
  in Obsidian         │  AI does the rest.   │               Claude API)
                      │                      │
  (Tech Team)         │  9 features total    │─────────────► (Ollama,
  edits YAML   ──────►│  (see Part 3)        │               local AI,
  to configure        │                      │               optional)
                      │                      │
  (Claude Desktop)    │  Complete product    │
  asks questions ────►│  ships June 2026     │
  via MCP chat        │                      │
                      └──────────────────────┘
                                │
                    reads from / writes to
                                │
               ┌────────────────┴────────────────┐
               ▼                                 ▼
     ┌──────────────────┐              ┌──────────────────┐
     │ Obsidian Vault   │              │ Knowledge Index  │
     │ (your notes on   │              │ (SQLite, local)  │
     │ disk)            │              │                  │
     └──────────────────┘              └──────────────────┘
```

**Problem-to-feature mapping:**
```
  Root cause            Solved by
  ─────────────────────────────────────────────────────────────────
  ① ACCUMULATION    →   Classify  (auto-files with confidence gate)
  ② NO TIME         →   All features (zero organizational effort)
  ③ TRANSCRIPTION   →   Capture   (AI reads + summarizes every drop)
  ④ LOST IDEAS      →   Search + Synthesis + Briefing (resurface + connect)
  ⑤ LOST TRACK OF TASK → Documentation + Briefings
```

---

## Part 3 — Feature Map: All 9 Capabilities of the Complete Product

```
  ┌────────────────────────────────────────────────────────────────────────────┐
  │                     AI-kms  —  Complete Product View                       │
  │                                                                            │
  │  INGEST                  ORGANIZE               RETRIEVE                  │
  │  ─────────────────       ──────────────         ──────────────────────    │
  │  ① Capture               ② Classify             ③ Search (semantic)       │
  │  Drop any file →         inbox notes →          find by meaning, not      │
  │  AI summarizes           right folder           keywords                  │
  │  + tags it               (with confidence       ④ Three-Tier Retrieval    │
  │                          gate: auto/review/     summary → snippet →       │
  │                          human)                 full note                 │
  │                                                                            │
  │  SURFACE                 EVOLVE                 INTERFACE                 │
  │  ─────────────────       ──────────────         ──────────────────────    │
  │  ⑥ Note Promotion        ⑧ Self-Learning        ⑤ MCP Server             │
  │  raw capture →           AI learns from         Claude Desktop can        │
  │  structured knowledge    your corrections       search + capture +        │
  │  (research note,                                classify via chat         │
  │  lesson learned,         ⑦ Documentation        ⑨ Briefing + Synthesis   │
  │  template)               auto-updated           daily digest of what      │
  │                          project pages          moved + weekly pattern    │
  │                                                 report                    │
  └────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 4 — Use Case Scenarios

### Scenario A: After a meeting — note lands in the right place automatically

*You type a quick meeting note and drop it into inbox. No filing needed.*

```
  You                          AI-kms                     Vault
  ─────                        ──────                     ─────
  drop "mtg-notes.md"
  into inbox/
        │
        └────────────────────► watches inbox
                                AI reads note
                                AI writes summary
                                AI suggests project:
                                  "Projects/ZalopayAPI"
                                  confidence 92% → AUTO
                                        │
                                        └─────────────── moves note to
                                                         Projects/ZalopayAPI/
                                                         renames it to:
                                                         "mtg-notes — Q2 API Roadmap.md"
                                                         body: UNCHANGED
                                                         frontmatter: summary + tags added

  You open Obsidian 10 min later.
  The note is already filed, summarized, and tagged.
  You did nothing.
```

---

### Scenario B: A PDF report — AI reads it, creates a searchable summary

*Finance sends you a Q2 report. Drop it. AI handles the rest.*

```
  You                          AI-kms                     Vault
  ─────                        ──────                     ─────
  drop "Q2-report.pdf"
  into inbox/
        │
        └────────────────────► AI reads PDF text
                                AI writes summary
                                no project context
                                → CLUELESS marker written:
                                inbox/.summaries/Q2-report.pdf.md
                                (status: pending-routing)

                                [Phase 2 classifies it]
                                confidence 88% → AUTO
                                        │
                                        └─────────────── PDF moved to:
                                                         Domain/Finance/attachment/
                                                         Q2-report.pdf

                                                         Summary note at:
                                                         Domain/Finance/attachment/
                                                         .summaries/Q2-report.pdf.md

  Searchable. Filed. You never opened the PDF.
```

---

### Scenario C: Searching your vault by meaning

*Three weeks later — you need to find something but can't remember where it was.*

```
  You type:  kms search "what were the API rate limit concerns?"
                    │
                    ▼
              AI-kms searches:
                keyword index (FTS5) — exact matches
                semantic index (embeddings) — meaning matches
                hybrid re-rank — best of both
                    │
                    │  starts at Hot tier (summaries only — fast)
                    ▼
              Returns 3 results with summaries
                    │
                    │  you ask: "show me full note for result 2"
                    ▼
              Escalates to Warm tier (matching snippet + context)

  What you find: notes you had forgotten existed.
  What you did NOT have to do: remember which folder, which app, which date.
```

---

### Scenario D: Asking questions through Claude Desktop

*"Catch me up on where the ZalopayAPI project stands."*

```
  You (in Claude Desktop chat)
        │
        │ "What are the open issues in ZalopayAPI right now?"
        ▼
  Claude Desktop  ──MCP──►  AI-kms MCP Server
                                    │
                                    │ kms_search("ZalopayAPI open issues")
                                    │ kms_search("ZalopayAPI risks decisions")
                                    ▼
                              searches vault
                              finds 6 relevant notes from past 3 weeks
                                    │
                                    ▼
  Claude Desktop  ◄── results ──  MCP Server

  Claude responds:
  "Based on your notes from the past 3 weeks:
   - 2 open technical risks (rate limiting, timeout handling)
   - 1 unresolved decision on auth approach
   - Upcoming deadline flagged in your Oct 15 meeting note"

  You never opened Obsidian.
  You never searched manually.
```

---

### Scenario E: Project documentation stays current automatically

*A new note is captured to the ZalopayAPI project. The living doc updates.*

```
  New note captured to Projects/ZalopayAPI/
          │
          ▼
    [Phase 6 — Documentation pipeline triggers]
          │
          reads all ZalopayAPI notes
          diffs against Documentation/ZalopayAPI.md
          proposes: add "API Rate Limit Risk" section
                    update "Last Activity" date
          │
          ▼ (if field has updated_by_human = true → SKIP that field)
          ▼ (else → write the proposal)
          │
          ▼
    Documentation/ZalopayAPI.md updated

  The project doc is always current.
  You never have to write a status update from scratch.
```

---

### Scenario F: Morning briefing — what needs your attention today

*You open Obsidian. The first thing you see is your AI digest.*

```
  7:00 AM — Briefing pipeline runs automatically
          │
          reads audit_log for past 24 hours
          reads what is still in inbox (needs human review)
          │
          ▼
    Vault/Briefings/2026/05_26.md

  ─────────────────────────────────────────────────────────
  Yesterday: 11 items captured, 9 auto-filed
  ─────────────────────────────────────────────────────────
  NEEDS YOUR REVIEW (2 items):
    → Q4-forecast.pdf   AI confidence 63% → Domain/Finance?
    → stakeholder-mtg   AI confidence 71% → Projects/Growth?
  ─────────────────────────────────────────────────────────
  SURFACED FROM VAULT:
    → "API retry logic" note (3 months old) — still unresolved?
  ─────────────────────────────────────────────────────────

  You spend 2 minutes on review instead of 30 minutes on filing.
```

---

### Scenario G: Weekly synthesis — patterns you would never have noticed

*Sunday evening. AI reads your whole week and finds what you missed.*

```
  Every Sunday — Weekly Synthesis pipeline runs
          │
          reads all notes captured this week
          reads audit_log for classification patterns
          asks AI: "what themes, contradictions, and open items appear?"
          │
          ▼
    Vault/Synthesis/2026/W21.md

  ─────────────────────────────────────────────────────────
  This week: 34 notes captured across 6 projects
  ─────────────────────────────────────────────────────────
  RECURRING THEME: "integration complexity" appeared in 4 notes
    across 3 different projects. Are these related?
  CONTRADICTION: Note from Mon says "auth decided", note from
    Thu says "auth approach still open". Which is current?
  ACTION ITEM PATTERN: 7 notes contained action items. None
    were marked as resolved.
  ─────────────────────────────────────────────────────────

  AI surfaces patterns. You decide what to do about them.
```

---

### Scenario H: AI gets smarter from your corrections

*You move a mis-classified note. AI learns not to repeat the mistake.*

```
  AI classified: "market-research.md" → Domain/Engineering  (confidence 74%)
  You moved it:  Domain/Finance/                             (your judgment)
          │
          ▼
  Watcher detects the manual move
  Logs correction: original → your choice → confidence score
          │
          ▼
  Next time classify.yaml prompt loads:
  includes your 5 most recent corrections as examples
  (few-shot learning from your preferences)
          │
          ▼
  Similar note next week: "market-analysis.md"
  AI classifies → Domain/Finance  (confidence 87%)
  → AUTO (no human review needed)

  The system adapts to how you think, not the other way around.
```

---

## Diagram Notes

| Term | Plain-English Explanation |
|---|---|
| **Manager** | Primary user. Non-technical executive with multiple responsibilities. Drops files, reads results in Obsidian. Never needs to organize manually. |
| **Tech Team** | Configures the system via YAML files (thresholds, routing, providers). No code changes needed for configuration adjustments. |
| **Claude Desktop** | An Anthropic AI app. When AI-kms MCP server is running, Claude Desktop connects to it so the manager can search and discuss vault contents through natural chat. |
| **Anthropic Claude API** | Cloud AI service. Called for summarization, classification, metadata extraction, promotion, documentation updates, synthesis. |
| **Ollama** | Local AI server. Optional fallback when cloud API is unavailable or content is too sensitive to leave the machine. |
| **Obsidian Vault** | A folder of markdown files on your disk. AI-kms watches it for drops and writes results back. Obsidian's UI reads the same folder. |
| **Knowledge Index** | SQLite database alongside the code. Stores: note index (for fast search), full audit log of every AI decision, human corrections (for self-learning). |
| **Confidence gate** | AI rates its own certainty (0–100%). ≥85% = auto-file with no human input. 60–85% = AI suggests, human confirms. <60% = flag for human decision. |
| **Hot / Warm / Cold tier** | Search cost levels. Hot = summary only (fast). Warm = matching snippet + context. Cold = full note. Always starts hot and escalates only if needed. |
| **updated_by_human** | A safety flag on each note. Once a human edits a field, the AI will never overwrite that field again — only propose changes for review. |
