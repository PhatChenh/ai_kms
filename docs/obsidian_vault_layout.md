## Obsidian vault layout (note that the outer folder could be named differently from "Vault")
```text
Vault/
├── Briefings/            ← daily AI reports on classification, vault status, and important tasks
│   └── 2026/
│       ├── 12_04.md
├── inbox/                      ← single drop zone
│   ├── report.pdf
│   └── .summaries/             ← pending-routing markers for CLUELESS binaries (AI-managed)
│       └── report.pdf.md       ← status=pending-routing; attachment_path → inbox/report.pdf
├── Projects/                   ← active work + its materials
│   └── Movies Q2 Strategy/
│       ├── CLAUDE.md            ← AI-maintained index with links to documents; user primary view (also project instructions/context for Claude products)
│       ├── <user notes>.md
│       └── attachment/          ← per-project binaries (PDF, docs, etc.)
│           ├── report.pdf
│           └── .summaries/      ← hidden from Obsidian; sibling .md files indexed here
│               └── report.pdf.md ← type=attachment-summary; attachment_path → binary
├── Domain/             ← durable knowledge per domain
│   ├── Movies/
│   │   ├── CLAUDE.md                  ← Basic context of the business line + index (also domain instructions/context for Claude products)
│   │   ├── context.yaml        ← people, metrics, vocabulary
│   │   ├── notes/              ← important materials, synthesized notes, industry reports, way of working, etc.
│   │   ├── attachment/          ← per-domain binaries, same structure as Projects
│   │   │   └── .summaries/
│   │   └── Archive/             ← archived projects under this domain (auto-managed by AI)
│   ├── Game/
│   ├── Bill Payment/
│   └── ...
├── Documentation/              ← one living page per active project — user primary interface; AI + user co-author; single source of truth for current project state
└── Synthesis/                  ← time-series reports (weekly journals, written by AI)
```
_(No global `attachment/` or `Archive/` — both are per-project / per-domain since Phase 1.5.)_
**Briefings**: Daily report on
- Note classification from inbox (where did the notes go, and if any notes unclear and need human audit)
- Important tasks/Deadline of today, from across all projects and business line (each business line get 5 tasks)

**inbox**: Place to dump all new knowledge/notes/email/chat sessions, etc. AI generates summaries, metadata, categorization, and moves to the correct folder in **Domain** or **Projects**.
- For .md files: categorize, add frontmatter, and move
- For .md files containing a Youtube/website link: summarize YT/website content, rename (if appropriate), categorize, add frontmatter, and move
- For non-md files (emails, docx, excel, pdf):
  - If AI can resolve which project or domain the file belongs to: write sibling `.md` at `Projects/<A>/attachment/.summaries/<file>.md` or `Domain/<D>/attachment/.summaries/<file>.md`; move binary to the matching `attachment/` folder
  - If AI cannot resolve (CLUELESS): binary stays in `inbox/`; write a pending-routing marker at `inbox/.summaries/<file>.md` with `status=pending-routing` and `attachment_path` pointing to the binary. Phase 2 Classify resolves these markers later.

**Projects**: current active projects of each **Domain**. Each project get its own folder, within each folder will have `CLAUDE.md` to help navigating all the notes and material related to the project contained in the subfolder `materials`.
- For AI: notes in `materials` will be the input to the model when answering or discussing with human, and will be the key ingredients for writing related synthesizing report in **Domain**, **Documentation** or **Synthesis** folders
- For user: these notes will also be the thinking space for human. To navigate the notes, user will use the `CLAUDE.md`. The index's content would be a table or list of material notes' titles, summaries of what it is, date created/added, etc. If user need a scratchpad, they could write inside the note in `material` folder, and the index would be updated to include links to those notes
- Note on the `CLAUDE.md`: Could be maintain by AI, or could be done using Dataview, and AI just need to update the right metadata for each note

**Domain**: contains all the domain-knowledge and long-term areas that users need to fulfill in their job. This could be:
- The business lines they are working on: Like Game, Movies, etc.
- The work-aspects that they want to improve: People development, Negotiation competencies
Each domain has its own folder, and each will have:
- `CLAUDE.md`: the basic context of the domain and index for navigating notes in the domain
- `context.yaml`: basic information like, PIC, metrics, vocabulary
- `notes` folder: contains important materials or domain-wide knowledge (industry report), synthesized report or reflection of current or past project, or the developing of the business domain, etc. This folder is the final destination of notes in **Documentation** once the project is not active anymore

**Documentation**: contains synthesized reports on current active projects. Each projects have one page. The AI will input this collaboratively with user. This report will be feed into the AI as the single source of truth knowledge about the current state of the project.
Pending decisions: Should AI update it or human update it? What if the current update documentation conflicts with newest or older notes in **Project** folder, will AI knows which one to trust and update the document? How often should the update cycle - daily or weekly? These will be answered later

**Synthesis**: Weekly report of AI about the user's work over the week. This is written by AI, based on AI's observation of the user, and is meant for the user to reflect on their work over the week

**attachment** (per-project / per-domain, no longer global): contains binary source files (PDF, DOCX, XLSX, etc.) for that project or domain. Each `attachment/` folder has a `.summaries/` subfolder where AI writes the sibling `.md` file. The sibling carries `type=attachment-summary` and `attachment_path` frontmatter pointing back to the binary. `.summaries/` is hidden from Obsidian's graph view but indexed by the KMS for search.

## How each folders are used and managed:
| Folder                          | AI                                                                                           | User                                                        |
| ------------------------------- | -------------------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| Briefings                       | Write daily reports                                                                          | Read to catch up with current work                          |
| inbox                           | Process drops; write pending-routing markers in `inbox/.summaries/` for CLUELESS binaries   | One place to dump all notes and binary files                |
| inbox/.summaries/               | Write & resolve pending-routing markers; hidden from user                                    | Invisible (Obsidian hidden folder)                          |
| Projects                        | Manage entirely by AI                                                                        | View notes & scratchpad for thinking                        |
| Projects/\<A\>/attachment/      | Move resolved binaries here                                                                  | Source material for reference                               |
| Projects/\<A\>/attachment/.summaries/ | Write sibling `.md` summary files here                                              | Invisible (Obsidian hidden folder); indexed by KMS search   |
| Domain                          | Co-author                                                                                    | Co-author                                                   |
| Domain/\<D\>/attachment/        | Same as Projects attachment                                                                  | Source material for reference                               |
| Domain/\<D\>/Archive/           | Auto-archive inactive projects                                                               | Read historical project records                             |
| Documentation                   | Co-author                                                                                    | Co-author; main interface                                   |
| Synthesis                       | Manage entirely by AI                                                                        | Read to reflect & find insights                             |
