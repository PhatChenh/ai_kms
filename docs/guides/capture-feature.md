Tính năng của Thư Đồng:

# Capture — how your files become a searchable knowledge base

**Capture** is the first thing that happens when you add a file to your
knowledge base. It reads your file, extracts the text, and asks the AI to
write a structured summary — so you can search and find it later, even if you
don't remember the filename.

Think of Capture as the system's "reading and note-taking" step. You drop in
a file. The AI reads it and writes down what it found. Everything after that
— searching, browsing, extracting knowledge — builds on top of what Capture
produced.

---

## What files can it handle?

| File type | Examples | How the system reads it |
|---|---|---|
| Markdown notes | `.md` | Reads the body text directly |
| PDFs | `.pdf` | Extracts all the text from every page |
| Word documents | `.docx` | Extracts the text content |
| Excel spreadsheets | `.xlsx` | Extracts cell values row by row |
| PowerPoint slides | `.pptx` | Extracts text from every slide |
| CSV files | `.csv` | Reads the rows as text |
| Web pages | `.html` | Strips the formatting and keeps the readable text |
| Email files | `.eml`, `.msg` | Extracts subject, sender, and body text |
| Images | `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp` | Asks a vision AI to describe what it sees |

**Size limit:** files larger than 50 MB are skipped — they're usually too big
for the AI to process meaningfully anyway.

---

## What happens step by step

When you add or update a file, the system runs through a fixed sequence of
steps. Each one has a purpose, and the order matters — the file's content is
saved to the database *before* the AI even looks at it, so nothing is ever
lost.

Here is the full journey:

```
  Your file
     │
     ▼
┌──────────────┐
│ 1. Extract   │  Read the text out of the file
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ 2. Dedup     │  Is this the exact same content we already have?
└──────┬───────┘    → Yes: stop here, nothing to do
       │            → No: continue
       ▼
┌──────────────┐
│ 3. Store raw │  Save the full text to the database NOW
└──────┬───────┘    (your file is safe regardless of what happens next)
       │
       ▼
┌──────────────┐
│ 4. Summarize │  Ask the AI to read the text and write a structured summary
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ 5. Index     │  Make every word searchable (keyword + semantic search)
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ 6. Classify  │  Later: extract structured facts (people, projects, decisions)
└──────────────┘
```

### Step 1 — Extract

The system picks the right "reader" for your file type. For a PDF, it
extracts every page's text. For a Word document, it pulls out the content.
For an image, there's no text to extract — so the system takes a different
path (see "What about images?" below).

### Step 2 — Dedup (skip duplicates)

Every file gets a digital fingerprint (a "content hash"). Before doing any
work, the system checks: do we already have this exact content? If yes, it
stops right here — no wasted AI calls, no duplicate entries. If you update
the file and the content changes, the fingerprint changes too, and the system
processes it again.

### Step 3 — Store raw (safety first)

The full extracted text is saved to the database immediately. This is the
"store first, summarize second" rule: even if the AI call in the next step
fails (network down, API error, etc.), your file's content is already safe
and searchable. The summary can always be filled in later.

### Step 4 — Summarize (the AI does the reading)

The system sends the extracted text to an AI (the "Housekeeping AI") with
instructions to produce a structured summary. The AI does NOT invent facts —
it only reports what it actually found in the text.

The summary always has five sections:

| Section | What it contains |
|---|---|
| **Overview** | 1–2 sentences: what this document is about |
| **Key points** | 2–5 bullet points of the most important facts |
| **Decisions** | Any choices or commitments recorded in the document |
| **Action items** | Tasks, TODOs, or next steps mentioned |
| **People mentioned** | Names or roles that appear in the text |

The AI also generates a short, human-readable title — like "Q2 Marketing
strategy review" — which becomes the document's title in search results.

### Step 5 — Index (make it findable)

The summary and full text are added to the search index. After this step,
you can find the file by searching for any word or phrase it contains, even
if you don't remember the filename.

### Step 6 — Classify (extract knowledge, runs later)

Capture hands off to a separate process called **Classify**. Classify reads
the summary and extracted text and pulls out structured facts — people, their
roles, project deadlines, decisions, policies. These facts go into a
separate "knowledge entries" table that powers the AI assistant's
understanding of your world. Classify runs in the background after capture
finishes, so you don't wait for it.

---

## What about images and scanned PDFs?

If a file has no extractable text — like a photo, a screenshot, or a
scanned PDF with no text layer — the system takes a different path:

1. The raw file bytes are saved to blob storage
2. A **vision AI** looks at the image and describes what it sees in detail
3. That description becomes the file's "summary" — so you can search for
   "dashboard showing Q2 revenue" and find the screenshot
4. If the image is too large (>10 MB) or the file type isn't supported for
   vision, the file is still stored — it just won't have an AI description
   until a later pass

---

## What if the AI fails?

The system is built so that **AI failure never means data loss**. The full
text is saved to the database *before* the AI is called (Step 3). If the AI
summarization fails — due to a network error, an API outage, or any other
reason — the system:

- Keeps the raw text safe in the database
- Logs the failure so it can be retried later
- Returns success anyway (your file is captured; the summary can be
  generated on a future pass)

You won't lose anything. At worst, a file might temporarily show up with its
filename as the title and no summary — but the full text is there and
searchable.

---

## What happens when you update a file?

If you edit a file and save it, the system detects the change, computes a new
fingerprint, and runs the full capture pipeline again. The old summary and
index entries are replaced with fresh ones. The old version's content is not
kept — the database always reflects the current file on disk.

If you haven't changed the content (same fingerprint), the system skips it
entirely — no wasted work.

---

## What happens when you delete or rename a file?

When you delete a file from your vault, the system removes its database
entry, search index, and any associated blob storage. When you rename or move
a file, the database entry updates to track the new path — the summary and
index entries follow the file to its new location.

---

## Key things to remember

- **You don't need to organize anything.** Put files wherever you want. The
  system watches everything and doesn't move your files.
- **Summaries are structured, not generic.** Every summary has the same five
  sections, so you know what to expect when browsing.
- **The AI doesn't guess.** It only reports what it actually found in your
  file. No invented facts, no opinions.
- **Search works immediately.** As soon as capture finishes, the file is
  findable by keyword and by meaning (semantic search).
- **AI failures are safe.** Your content is stored before the AI is called.
  Nothing is ever lost because of an API error.
