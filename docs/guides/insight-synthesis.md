Tính năng của Thư Đồng:

# Insight synthesis — the system actively looks for patterns and findings

**Insight synthesis** is what happens after a file is captured. Once the
system has read and summarized a document, it doesn't stop there — it
actively mines that document for structured knowledge. It compares what it
finds against everything it already knows, extracts new facts, updates
outdated ones, and retires things that are no longer true.

Think of it as the difference between filing a document and actually reading
it. Capture is the filing step — the document is stored, summarized, and made
searchable. Insight synthesis is the reading step — the system goes through
the content, picks out the people, projects, decisions, and facts, and weaves
them into your growing knowledge base.

This happens automatically in the background. You don't need to ask for it.
Every new or updated document triggers the process.

---

## The three knowledge dimensions

The system organizes all knowledge into three **dimensions** — categories
that tell the AI what kind of information to look for. Each dimension has
specific **tags** (sub-categories) that the AI must use. The AI cannot invent
new dimensions or tags — this keeps the knowledge base consistent.

| Dimension | What the AI looks for | Allowed tags |
|---|---|---|
| **People** | Who leads or owns the topic — name, title, role, or persona | `role`, `other` |
| **Projects** | Project status, timeline, milestones, deliverables | `status`, `timeline`, `other` |
| **Domains** | The functional area the knowledge belongs to (e.g. Finance, Engineering, Marketing) | `other` |

Every dimension has an `other` tag as a catch-all — for facts that belong to
the dimension but don't fit a specific tag. This keeps the system flexible
without letting the AI create its own categories.

The AI receives specific **guidance** for each dimension. For example, for
People it's told: "Look for who leads or owns the topic — name, title, org,
or persona." For Projects: "Look for project status, timeline, milestones, or
deliverable references." This guidance steers the AI toward finding the right
kind of information in each document.

---

## How extraction works — step by step

When a document is ready for insight synthesis, the system runs through a
careful seven-step process. Each step has a purpose, and the system never
skips ahead.

```
  Document (from capture)
     │
     ▼
┌──────────────────┐
│ 1. Read content  │  Choose the best text to analyze (full body or summary)
└──────┬───────────┘
       │
       ▼
┌──────────────────┐
│ 2. Load context  │  Pull up everything the system already knows, organized by dimension
└──────┬───────────┘
       │
       ▼
┌──────────────────┐
│ 3. Inject lessons│  Feed in past corrections as teaching examples for the AI
└──────┬───────────┘
       │
       ▼
┌──────────────────┐
│ 4. Extract facts │  Ask the AI: "given this document and what you already know,
└──────┬───────────┘  what's new, what's changed, what's outdated?"
       │              (One AI call per dimension)
       ▼
┌──────────────────┐
│ 5. Validate       │  Check every fact: correct format, allowed tags, confidence in range
└──────┬───────────┘
       │
       ▼
┌──────────────────┐
│ 6. Write entries  │  Merge with existing knowledge — detect duplicates, combine sources,
└──────┬───────────┘  respect the overwrite guard
       │
       ▼
┌──────────────────┐
│ 7. Stamp or retry │  All clean? Stamp the document done. Any failure? Retry with
└──────────────────┘  feedback, up to 3 attempts, then park for human attention.
```

### Step 1 — Read the content

The system picks the best version of the document to analyze. If the full
extracted text is short enough to fit in the AI's working memory (under
~10,000 tokens — roughly 40,000 characters), it uses the full text. If the
document is very long, it falls back to the AI-generated summary. This keeps
the AI call fast and focused.

### Step 2 — Load the context

Before asking the AI to extract new facts, the system loads everything it
already knows — organized by dimension. For each dimension, it pulls the most
relevant existing facts (capped at 50 per dimension to keep the AI's context
manageable). This is crucial: the AI sees the new document **alongside** the
existing knowledge, so it can spot what's genuinely new, what updates
something old, and what contradicts what was previously known.

The system also loads any **comments** you've added to existing facts, so the
AI has your additional context when deciding what to do.

### Step 3 — Inject lessons from past mistakes

If you've previously corrected facts that the AI got wrong (and tagged the
correction as an "AI error"), the system pulls the most relevant ones and
shows them to the AI as teaching examples. It selects corrections that match
the current dimension and the entities mentioned in the document, prioritizing
the most recent ones. The AI literally sees: "You got this wrong before —
here's the correct version. Don't make the same mistake." (See "How the
system teaches itself" below for details.)

### Step 4 — Extract facts

This is the core AI call. The system asks the AI one question per dimension:
"Given this document and everything you already know about People, what's
new, what's changed, and what's outdated?" The AI responds with a structured
list of actions.

### Step 5 — Validate every fact

Every fact the AI returns goes through a strict validation check. The system
verifies:

- The format is correct (JSON array with the right fields)
- Every required field is present (entity, tag, fact, confidence)
- Tags come from the allowed list for that dimension
- Confidence scores are between 0.0 and 1.0
- New facts don't accidentally include database IDs

Any validation failure stops the process for that dimension and triggers a
retry — the error is fed back to the AI so it can fix its mistake on the next
attempt.

### Step 6 — Write entries

Validated facts are written to the knowledge base. This is where the system
gets smart about merging:

- **Twin detection:** if the AI proposes a "new" fact that matches an
  existing entry (same entity, dimension, and tag), the system folds the new
  information into the existing entry instead of creating a duplicate. It
  merges the source lists and reasoning.
- **Overwrite guard:** if the AI wants to update an existing fact that you've
  previously confirmed (trust above 0.50), the system blocks the overwrite
  and creates a competing "pending" entry instead. You resolve the conflict
  later.
- **Source merging:** every fact carries a list of which documents it was
  extracted from. When two documents contribute to the same fact, both
  sources are recorded — so you can always trace back to where a fact came
  from.

### Step 7 — Stamp or retry

If every dimension processed cleanly, the document is stamped as "classified"
and the system moves on. If any dimension failed, the system records the
error and retries the entire document — up to **3 attempts**. On each retry,
the AI sees the previous failure as feedback: "Your last attempt failed
because of X. Avoid repeating the same mistake." If all 3 attempts fail, the
document is **parked** — set aside for human attention rather than silently
dropped.

---

## Three actions the AI can take

For each fact in a document, the AI must decide on one of three actions:

| Action | What it means | Example |
|---|---|---|
| **New** | This fact has never been seen before | A document mentions "Sarah Chen" as "VP of Design" — the system creates a new People entry for Sarah with role=VP of Design |
| **Update** | The AI has new or better information about an existing fact | A previously captured document said "Project X deadline is July 15." A new document says "the deadline moved to August 1." The AI updates the existing entry. |
| **Retire** | An existing fact is explicitly contradicted or superseded | A document states "the Q2 campaign has been cancelled." The AI retires the existing fact about the Q2 campaign with the reason: "Campaign was cancelled per the June 10 status update." |

The AI is instructed to be conservative:
- It does NOT re-state facts that are already captured. If the document adds
  nothing new, the AI returns an empty list — silence is better than noise.
- It does NOT retire a fact just because this document doesn't mention it.
  Retirement only happens when the document **explicitly** contradicts or
  overrides the old fact.
- It assigns a **confidence score** (0.0–1.0) based on how certain the
  information is: 0.9+ for explicit statements, 0.7–0.9 for reasonable
  inferences, below 0.7 for speculative claims.

---

## How the system handles conflicts

When new information arrives, the system doesn't just overwrite everything.
It handles conflicts carefully:

### Twin detection

Before creating a "new" entry, the system checks: does an entry already exist
for this exact combination of entity, dimension, and tag? If yes, the new
information is **folded into the existing entry** — no duplicate is created.
The sources are merged (both documents are credited), the reasoning is
combined, and the fact text is updated if the new version is better.

### Overwrite guard

If the existing entry has high trust (above 0.50 — meaning you've confirmed
it), the system **blocks** the overwrite entirely. Instead, it creates a
separate **pending** entry with the new information. You see both side by
side and decide which one is correct. The AI never silently replaces something
you've verified. This is the same overwrite guard described in the
self-learning guide.

### Source traceability

Every fact carries a list of which documents it came from. When the system
merges two entries, it combines the source lists. When you browse a fact, you
can see every document that contributed to it — and if something looks wrong,
you know exactly where to look.

---

## The self-correcting retry loop

AI extraction isn't perfect on the first try. The AI might return malformed
JSON, forget a required field, or use a tag that doesn't exist. The system
handles this with a **self-correcting retry loop**:

1. **First attempt** — the AI extracts facts for all dimensions
2. If any dimension fails (bad JSON, missing fields, invalid tags) → the
   error is recorded
3. **Second attempt** — the entire document is reprocessed. The AI sees the
   previous error as feedback: "Your last attempt failed because the JSON was
   malformed. Avoid repeating the same mistake."
4. If it fails again → **third attempt** with updated feedback
5. After 3 failures → the document is **parked**. It's not deleted or
   ignored — it's set aside with a record of what went wrong, waiting for
   human attention or a future system improvement.

This means nothing is ever silently dropped. Every document either gets
classified successfully or is parked with a clear record of what failed.

---

## The catch-up scan — nothing falls through the cracks

When the system starts up (or restarts after being down), it runs a
**catch-up scan**. It looks for every document that hasn't been classified
yet — whether because the system was offline, a document was added during
downtime, or a previous classification attempt was parked — and adds them all
to the processing queue.

This is like a diligent analyst coming back from vacation and going through
everything that arrived while they were away. You don't need to remember
which documents need processing. The system finds them and works through them
in order.

---

## How the system teaches itself

Every time you correct a fact and mark the reason as "the AI got this wrong"
(`ai_error`), the correction goes into a teaching library. When the system
processes a new document, it automatically:

1. **Selects relevant corrections** — from the library of past AI errors, it
   picks the ones most relevant to the current document. It scores them by:
   - +3 points if the correction is for the same dimension
   - +2 points if the correction involves an entity mentioned in the document
   - +1 point for recency (newer corrections are more relevant)
2. **Formats them as teaching examples** — each correction becomes a short
   lesson: "The AI incorrectly said X. The correct fact is Y. Here's why."
3. **Injects them into the extraction prompt** — the AI sees these examples
   right before it reads the document, alongside explicit instructions:
   "Previous extraction mistakes to avoid."

This is the "learning" in self-learning. The AI literally sees its past
mistakes and the correct answers, and adjusts its extraction behavior
accordingly. The more you correct, the more teaching examples accumulate, and
the more accurate future extractions become — without anyone having to
rewrite the AI's instructions.

---

## Reports — the final synthesis

While the day-to-day work of insight synthesis happens automatically
document-by-document, the system can also step back and produce broader
findings on demand. These are the **reports** — AI-generated analyses that
look across your entire knowledge base:

| Report | What it synthesizes |
|---|---|
| **Correction summary** | Are corrections trending down over time? If yes, the system is getting more accurate at extracting knowledge. |
| **Knowledge health** | How many facts per dimension? How many are confident vs pending vs retired? Which areas are thin? |
| **Volatile entries** | Which facts keep getting corrected? These might represent genuinely fluid information or persistent AI blind spots. |
| **Coverage gaps** | Which documents produced zero knowledge entries? Which dimensions are underrepresented? |
| **Conflicts** | Where do contradictory facts exist — the overwrite guard in action, waiting for your review. |

Each report is generated fresh when you ask for it, by an AI that reads the
current state of your knowledge base and writes a structured analysis. The
reports are saved so you can track trends over time.

---

## Key things to remember

- **It works automatically.** You don't need to ask for insight synthesis.
  Every new or updated document triggers the process in the background.
- **The AI compares before it decides.** Every extraction considers both the
  new document and everything the system already knows. The AI doesn't
  process documents in isolation.
- **Three actions, carefully chosen.** The AI can create new facts, update
  existing ones, or retire outdated ones. It's instructed to be conservative
  — silence is better than noise.
- **Your corrections teach the system.** Past AI mistakes become teaching
  examples that improve future extractions. The system gets more accurate the
  more you use it.
- **Conflicts are surfaced, not silenced.** When new information contradicts
  something you've verified, the system creates a pending entry for your
  review instead of overwriting. You're always in control.
- **Every fact is traceable.** Each knowledge entry links back to the
  document(s) it came from. If something looks wrong, you know exactly where
  to investigate.
- **Nothing is silently dropped.** Documents that fail classification are
  retried up to 3 times with feedback, then parked for human attention. The
  catch-up scan ensures nothing falls through the cracks after downtime.
- **Reports give you the big picture.** When you want to step back and see
  how your knowledge base is doing, the five report types synthesize
  everything into clear findings.
