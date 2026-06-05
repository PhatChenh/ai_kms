# AI-kms System Story
_A plain-English guide for non-technical readers._

<!-- ARCH-STORY:LEGEND -->
**How to read these diagrams**

```
  Boxes    = steps or places, plain English
  ───►     = what happens next / where things go
  ┌ ─ ─ ┐  = not built yet (coming soon)
  ✓ / →    = built / coming in the Phases diagram
```

<!-- /ARCH-STORY:LEGEND -->

---

<!-- ARCH-STORY:STORY -->
AI-kms watches a folder on your computer. Drop any file into it — a PDF, a meeting note, an email, a spreadsheet, anything — and the system takes over. It reads the file, writes a plain-English summary, tags it, and files it in the right project folder. Later you can search your entire library by meaning ("what do I know about stakeholder resistance?"), ask questions in Claude Desktop, or read a morning digest of everything that arrived yesterday. Every AI decision is logged and reversible. If you edit a note by hand, the AI will never silently overwrite it.

**What's built now:** The system watches your vault, reads every new file dropped into inbox, writes a summary and tags into the frontmatter, routes files to the right attachment folder, and keeps everything in sync as files move or get deleted. A 7-stage repair command can fix any drift between the vault and the database. This is the Capture phase — it covers nine file types: notes, PDFs, Word docs, spreadsheets, presentations, CSVs, web pages, emails, and Outlook messages. Images are recognised but not yet summarised (coming with a vision-capable AI model).

**What's coming next:** Classify takes the summaries Capture wrote and files each note into the right project or domain — automatically when the AI is confident, or prompting you to confirm when it is not. After that: Search (find anything by meaning), MCP Chat (ask questions through Claude Desktop), and then promotion, project docs, self-learning, and daily briefings.

Everything stays on your machine. The only data that leaves is the text sent to the AI for summarisation.
<!-- /ARCH-STORY:STORY -->

---

<!-- ARCH-STORY:PART1 -->
## The problem this solves

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
  │  ⑤ LOST TRACK   — many ongoing projects with accumulating to-dos     │
  └──────────────────────────────────────────────────────────────────────┘
```

<!-- /ARCH-STORY:PART1 -->

---

<!-- ARCH-STORY:PART2 -->
## How the system fits in

The AI handles problems ①②③. The Briefing and Synthesis handle ④ and ⑤.
You only make judgment calls the AI is uncertain about.

```
                      ┌──────────────────────┐
  (Manager)           │ AI-kms               │
  drops files ───────►│                      │
  reads results       │  Drop a file.        │─────────────► (Anthropic
  in Obsidian         │  AI does the rest.   │               Claude API)
                      │                      │
  (Tech Team)         │  9 features total    │─────────────► (Ollama,
  edits YAML   ──────►│  (see feature map)   │               local AI,
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
  ④ LOST IDEAS      →   Search + Synthesis + Briefing
  ⑤ LOST TRACK      →   Documentation + Briefings
```

<!-- /ARCH-STORY:PART2 -->

---

<!-- ARCH-STORY:PART3 -->
## All 9 capabilities of the complete product

```
  ┌────────────────────────────────────────────────────────────────────────────┐
  │                     AI-kms  —  Complete Product View                       │
  │                                                                            │
  │  INGEST                  ORGANIZE               RETRIEVE                  │
  │  ─────────────────       ──────────────         ──────────────────────    │
  │  ① Capture               ② Classify             ③ Search (semantic)       │
  │  Drop any file →         inbox notes →          find by meaning, not      │
  │  AI summarizes           right folder           keywords                  │
  │  + tags it               (confidence gate:      ④ Three-Tier Retrieval    │
  │                          auto / confirm /       summary → snippet →       │
  │                          human decides)         full note                 │
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

<!-- /ARCH-STORY:PART3 -->

---

<!-- ARCH-STORY:PART4 -->
## How the system works in practice

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
                                  high confidence → AUTO
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
                                high confidence → AUTO
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
    [Phase 7 — Documentation pipeline triggers]
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

  ─────────────────────────────────────────────────────
  Yesterday: 11 items captured, 9 auto-filed
  ─────────────────────────────────────────────────────
  NEEDS YOUR REVIEW (2 items):
    → Q4-forecast.pdf   AI not confident → Domain/Finance?
    → stakeholder-mtg   AI not confident → Projects/Growth?
  ─────────────────────────────────────────────────────
  SURFACED FROM VAULT:
    → "API retry logic" note (3 months old) — still unresolved?
  ─────────────────────────────────────────────────────

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

  ─────────────────────────────────────────────────────
  This week: 34 notes captured across 6 projects
  ─────────────────────────────────────────────────────
  RECURRING THEME: "integration complexity" appeared in 4 notes
    across 3 different projects. Are these related?
  CONTRADICTION: Note from Mon says "auth decided", note from
    Thu says "auth approach still open". Which is current?
  ACTION ITEM PATTERN: 7 notes contained action items. None
    were marked as resolved.
  ─────────────────────────────────────────────────────

  AI surfaces patterns. You decide what to do about them.
```

---

### Scenario H: AI gets smarter from your corrections

*You move a mis-classified note. AI learns not to repeat the mistake.*

```
  AI classified: "market-research.md" → Domain/Engineering  (low-medium confidence)
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
  AI classifies → Domain/Finance  (high confidence)
  → AUTO (no human review needed)

  The system adapts to how you think, not the other way around.
```

<!-- /ARCH-STORY:PART4 -->

---

<!-- ARCH-STORY:DIAGRAM:PHASES -->
## What's built vs coming

```
  BUILT TODAY          COMING NEXT
  ────────────         ────────────
  ✓ Foundations        → Classify
  ✓ Capture            → Search
                       → MCP Chat
                       → Promotion
                       → Project Docs
                       → Self-Learning
                       → Daily Briefing
                       → Weekly Synthesis

  ────────────────────────────────────────────
  Milestone: Capture + Classify + Search + MCP — ~15 June 2026
  Milestone: Full product ships — 30 June 2026
```

<!-- /ARCH-STORY:DIAGRAM:PHASES -->

---

<!-- ARCH-STORY:DIAGRAM:STORAGE -->
## Where your stuff is stored

```
  ┌────────────────────────┐   ┌────────────────────────┐
  │  YOUR VAULT            │   │  AI MEMORY             │
  │  (Obsidian folder)     │   │  (database on disk)    │
  │                        │   │                        │
  │  • Your original files │   │  • Search index        │
  │  • AI summary notes    │   │  • Every AI decision   │
  │  • Project folders     │   │  • Your corrections    │
  └────────────────────────┘   └────────────────────────┘

  Both stay on your machine. Nothing is sent to the cloud
  except text sent to AI for summarisation.
```

<!-- /ARCH-STORY:DIAGRAM:STORAGE -->

<!-- ARCH-STORY:SUPPORT count=0 -->
<!-- /ARCH-STORY:SUPPORT -->

---

_Last updated: June 2026 (Phase 1 + sub-phases complete — 956 tests. Phase 2 starting). Technical reference: `docs/architecture/overall_design.md` and `docs/architecture/system_diagram.md`._
