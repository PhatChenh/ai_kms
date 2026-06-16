# Always-available context — your knowledge base, everywhere you are

**Always-available context** means your AI assistant knows what you know —
on any device, at any time. You connect your Claude client (desktop, web, or
mobile) to the knowledge system once, and from then on the AI already
understands your projects, your people, your decisions, and your documents —
without you having to re-explain anything.

Think of it as a second brain that follows you. You add files on your laptop.
The system reads them, summarizes them, and extracts the key facts. Then,
when you open Claude on your phone and ask "what's the status of the Q2
launch?", the AI already has the answer — because the knowledge lives in the
cloud, not on one device.

---

## How you connect

The connection between your Claude client and the knowledge system uses a
standard called **MCP** (Model Context Protocol). In practice, this is a
one-time setup:

1. Your Claude client is configured to connect to the KMS knowledge server
2. From that moment on, the knowledge system appears as a set of **tools**
   the AI can use — like search, read, and save
3. You don't call the tools yourself — the AI decides when to use them based
   on what you ask

The setup is done once per device (Claude Desktop, claude.ai web, Claude
mobile app). After that, the AI has access to your full knowledge base
wherever you are.

---

## The discovery workflow

When you start a conversation and ask a question that touches your knowledge
base, the AI follows a natural three-step pattern. You don't need to know the
steps — the AI handles them — but understanding them helps you see what's
happening behind the scenes.

### Step 1 — "What do I know?" (kms_vault_info)

The AI first gets a map of your knowledge base. It sees:

- **What entities exist** — people, projects, domains — grouped by category,
  with counts showing how many facts are known about each
- **Key facts** — the most important, highest-confidence facts across every
  category, ranked so the most relevant ones surface first
- **What's new** — how many recently-added files are waiting to be processed

This is like the AI opening a dashboard before diving into specifics. It
gives the AI a sense of what's available without searching blindly.

### Step 2 — "Find what's relevant" (kms_search)

Now the AI searches across both of your knowledge stores simultaneously:

- **Facts** — the structured knowledge extracted from your documents (people,
  roles, deadlines, decisions, policies)
- **Documents** — your original files, with their AI-generated summaries

The search understands both exact words and meaning. You can ask "what did we
decide about the marketing budget?" and the system finds the relevant facts
and documents even if "marketing budget" appears in different words.

The AI sees results as **cards** — each one has a title, a snippet, a
relevance score, and an ID number. The AI can refine the search (narrow by
project, by date range, by folder) if the first results aren't what you need.

### Step 3 — "Read the details" (kms_inspect)

Once the AI identifies which documents or facts are relevant, it drills in
for the details. It can read at three levels (see "Three ways to read a
document" below), starting with the summary and going deeper when needed.

---

## The seven tools — what they do for you

The knowledge system gives your AI assistant seven capabilities. You don't
call them directly — the AI uses them on your behalf. Here's what each one
does, in plain English:

| Tool | What it does | When the AI uses it |
|---|---|---|
| **Discover** | Shows a map of everything the system knows: people, projects, domains, recent activity | At the start of a conversation, so the AI knows what's available |
| **Search** | Finds facts and documents matching your question, across both structured knowledge and original files | Whenever you ask "what do we know about X?" or "find the document about Y" |
| **Inspect** | Reads a specific document — either its AI summary, its full text, or tells you where the original file lives | When search finds a promising result and the AI needs more detail to answer you |
| **Write** | Saves a new insight from your conversation into the knowledge base | When you share a decision, observation, or idea worth keeping |
| **Correct** | Fixes a fact the system got wrong — change the fact itself, its category, its status, or retire it | When you spot an error and want the system to learn from it |
| **Comment** | Adds your own note to a knowledge entry for context | When you want to add nuance that the AI should consider in the future |
| **Reports** | Generates a health report on your knowledge base — coverage gaps, frequently-corrected facts, conflicts | When you want a bird's-eye view of how your knowledge base is doing |

The AI is instructed to be **proactive** with Write (suggesting saving when
you share an insight) and **careful** with Correct (always confirming with
you before changing a fact).

---

## Three ways to read a document

When the AI needs to read a document, it can access it at three levels of
depth:

| Mode | What you get | Availability |
|---|---|---|
| **Summary** | The AI-generated 5-section digest (Overview, Key Points, Decisions, Action Items, People Mentioned) | Always — any device, any time |
| **Full text** | The complete extracted text of the original document | Always — any device, any time |
| **Original file** | The path to the actual file on your laptop | Only when your laptop is open and connected |

The AI starts with the summary. If that's enough to answer your question, it
stops there — fast and cheap. If it needs more detail, it reads the full
text. It only asks for the original file path when you need to actually open
or edit the file on your laptop.

For images and scanned PDFs, the "full text" is actually a detailed visual
description written by a vision AI — so you can search for "dashboard showing
Q2 revenue" and find the right screenshot, then read what's in it.

---

## Where does it live?

The knowledge server runs on **cloud infrastructure**, not on your laptop. It
stores everything in a database that is backed up to cloud storage — so your
knowledge base survives even if your laptop doesn't.

Your files (the originals on your laptop) are still yours. The system reads
them but never moves or changes them. What lives in the cloud is the
*extracted knowledge* — summaries, facts, and search indexes generated by the
AI from your files.

The connection between your laptop and the cloud is handled by a thin
background program (the **daemon**) that watches your files and uploads new
or changed content. When your laptop is closed, the daemon pauses — but
everything already in the cloud keeps working.

---

## What works when your laptop is closed?

Because the knowledge server runs in the cloud, most things work 24/7 from
any device:

| Capability | Laptop open | Laptop closed |
|---|---|---|
| Discover (map of your knowledge) | ✓ | ✓ |
| Search facts and documents | ✓ | ✓ |
| Read summaries and full text | ✓ | ✓ |
| Save new insights (Write) | ✓ | ✓ |
| Correct facts | ✓ | ✓ |
| Comment on entries | ✓ | ✓ |
| Generate reports | ✓ | ✓ |
| New files being captured | ✓ | ✗ — paused until laptop reconnects |
| Access original files | ✓ | ✗ — files are on the closed laptop |

The key insight: **everything that matters for answering questions works all
the time.** The only things that pause are ingesting new files (because the
daemon isn't running to detect them) and accessing the original files on disk
(because the laptop is closed). But all the knowledge already extracted —
summaries, facts, decisions, people — is always available.

When you open your laptop again, the daemon reconnects, catches up on any
file changes that happened while it was away, and the system resumes
capturing and classifying.

---

## Key things to remember

- **One setup, everywhere.** Connect each Claude client once, and your
  knowledge base is available on desktop, web, and mobile.
- **The AI knows before you ask.** When you start a conversation, the AI
  already has a map of your knowledge — it doesn't search blind.
- **Two kinds of knowledge.** The system stores both your original documents
  (with summaries) and structured facts extracted from them (people,
  projects, decisions). Search covers both.
- **You don't call the tools.** The AI decides when to search, read, or save.
  You just ask questions and share insights naturally.
- **Three reading levels.** Summary for quick answers, full text for depth,
  original file when you need to open something locally.
- **Cloud means always-on.** The knowledge server doesn't run on your laptop.
  Close it, and your knowledge base is still answering questions — on your
  phone, on the web, anywhere.
- **Your files stay your files.** The system reads them but never moves or
  changes them. The cloud stores the AI's *understanding* of your files, not
  the files themselves.
