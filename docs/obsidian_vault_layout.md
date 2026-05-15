## Obsidian vault layout (note that the outer folder could be named differently from "Vault")
```text
Vault/
├── Briefings/            ← place to report on the classification, vault status + Daily briefing (all in one place view of the most important information)
│   └── 2026/
│       ├── 12_04.md
├── inbox/                      ← single drop zone
├── Projects/                   ← active work + its materials
│   └── Movies Q2 Strategy/
│       ├── project_index.md    ← AI-maintained index with link directly to the document, user primary view
│       ├── materials/                ← captures, emails, transcripts moved from inbox after categorizatoin by AI + notes from users. AI will use information in this folder as the sources for their responses
├── Domain/             ← durable knowledge per domain
│   ├── Movies/
│   │   ├── domain_index.md           ← Basic context of the business line + index
│   │   ├── context.yaml        ← people, metrics, vocabulary
│   │   └── notes/              ← important materials, synthesize notes on active and past projects, notes on people, way of working, etc.
│   ├── Game/
│   ├── Bill Payment/
│   └── ...
├── Documentation/              ← one living page per active project - user primary interface, collaborative input by AI and user. AI always read this to get the newest up to date information of project
├── Synthesis/                  ← time-series reports (weekly journals)
├── attachment/                 ← folder for all non-md files (PDF, docs)
└── Archive/                    ← auto-archived, invisible to her
```
**Briefings**: Daily report on
- Note classification from inbox (where did the notes go, and if any notes unclear and need human audit)
- Important tasks/Deadline of today, from across all projects and business line (each business line get 5 tasks)

**inbox**: Place to dump all the new knowledge/notes/email/chat sessions, etc. and let AI to generate summaries, metadata, categorization, and move to the correct folder in **Domain**. 
- For .md files: categorize, add frontmatter, and move
- For .md files containing a Youtube/website link: Summarize YT/website content, rename (if appropriate), categorize, add frontmatter, and move
- For non-md files (emails, docx, excel, pdf): create a sibling .md file, then:
	- For the .md file: summarize the content of the non-md file (MUST HAVE link to the source material, using Obsidian link convention of brackets), rename (if appropriate, and make sure .md file and the source material have corresponding names - could be exact naming or not, but regardless should have some convention to easily identify and detect connection between them), categorize, add frontmatter, and move
	- for the non-md file: rename (if appropriate), and move to **attachment**

**Projects**: current active projects of each **Domain**. Each project get its own folder, within each folder will have `project_index.md` to help navigating all the notes and material related to the project contained in the subfolder `materials`.
- For AI: notes in `materials` will be the input to the model when answering or discussing with human, and will be the key ingredients for writing related synthesizing report in **Domain**, **Documentation** or **Synthesis** folders
- For user: these notes will also be the thinking space for human. To navigate the notes, user will use the `project_index.md`. The index's content would be a table or list of material notes' titles, summaries of what it is, date created/added, etc. If user need a scratchpad, they could write inside the note in `material` folder, and the index would be updated to include links to those notes
- Note on the `project_index.md`: Could be maintain by AI, or could be done using Dataview, and AI just need to update the right metadata for each note

**Domain**: contains all the domain-knowledge and long-term areas that users need to fulfill in their job. This could be:
- The business lines they are working on: Like Game, Movies, etc.
- The work-aspects that they want to improve: People development, Negotiation competencies
Each domain has its own folder, and each will have:
- `domain_index.md`: the basic context of the domain and index for navigating notes in the domain
- `context.yaml`: basic information like, PIC, metrics, vocabulary
- `notes` folder: contains important materials or domain-wide knowledge (industry report), synthesized report or reflection of current or past project, or the developing of the business domain, etc. This folder is the final destination of notes in **Documentation** once the project is not active anymore

**Documentation**: contains synthesized reports on current active projects. Each projects have one page. The AI will input this collaboratively with user. This report will be feed into the AI as the single source of truth knowledge about the current state of the project.
Pending decisions: Should AI update it or human update it? What if the current update documentation conflicts with newest or older notes in **Project** folder, will AI knows which one to trust and update the document? How often should the update cycle - daily or weekly? These will be answered later

**Synthesis**: Weekly report of AI about the user's work over the week. This is written by AI, based on AI's observation of the user, and is meant for the user to reflect on their work over the week

**attachment**: single source to contain all the non-md files. Used only when AI or human need to dig into the source material.

## How each folders are used and managed:
| Folder        | AI                                                           | User                                                      |
| ------------- | ------------------------------------------------------------ | --------------------------------------------------------- |
| Briefings     | Write daily reports                                          | Read to catch up with current work                        |
| inbox         | Listen to input and do Capture and Categorization processing | One place to dump all notes and knowledge related to work |
| Projects      | Manage entirely by AI                                        | View notes & scratchpad for thinking                      |
| Domain        | Co-author                                                    | Co-author                                                 |
| Documentation | Co-author                                                    | Co-author, and main interface                             |
| Synthesis     | Manage entirely by AI                                        | Read to reflect & finding insights                        |
| attachment    | Write .md summarize note, and move the source material here  | Source material for reference                             |
