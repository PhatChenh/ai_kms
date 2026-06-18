Tính năng của Thư Đồng:

# Self-learning — the system gets smarter every time you correct it

**Self-learning** is what separates this knowledge system from a simple
search engine. Every time you correct a fact — confirm it, fix it, or dismiss
it — the system learns. It adjusts its trust in what it knows, it blocks the
AI from overwriting things you've verified, and it uses your corrections to
get better at extracting knowledge from new documents.

Think of it as training a junior analyst who works for you. At first, the
analyst reads your documents and takes notes — some accurate, some not quite
right. When you correct them, they learn: "The boss confirmed this, so I'm
more confident about similar things. The boss rejected that, so I'll be more
careful next time." Over weeks and months, the analyst needs fewer
corrections because they've learned your standards.

The self-learning system has four parts: **correcting facts**, **trust
scores**, **the overwrite guard**, and **reports**. Together they form a
feedback loop that makes your knowledge base more accurate over time.

---

## How you correct things

There are nine ways to correct a fact, but they fall into three natural
groups. You don't need to memorize them — the AI will suggest the right one
when you spot something wrong.

### Group 1 — Validate or dismiss a fact

These are the most common corrections. The AI extracts a fact from a
document, and you tell it whether the fact is right or wrong.

| Operation | What it means | Example |
|---|---|---|
| **Confirm** | The fact is correct. Boost its trust score. | "Yes, Anthony is the Product Lead — that's right." |
| **Reject** | The fact is wrong. Lower its trust and retire it. | "No, the Q2 deadline is NOT June 30 — that's wrong." |
| **Revise** | The fact is mostly wrong but the topic is important. Retire the old one and create a corrected version. | "The budget is $500K, not $300K. Here's the correct number." |

Confirm says "this is right, I trust it more now." Reject says "this is wrong
— get rid of it." Revise says "the idea is right but the details are wrong —
here's the fix."

### Group 2 — Change a detail

Sometimes the fact is close but a specific field needs fixing.

| Operation | What it means |
|---|---|
| **Change the fact text** | Rewrite the fact itself to be more accurate |
| **Change the tag** | Move the fact to a different sub-category (e.g. from "other" to "deadline") |
| **Change the entity** | Reassign the fact to a different person, project, or domain |

These are surgical fixes — you're not dismissing the fact, just refining it.

### Group 3 — Manage status

Facts have a status: **confident** (the system is sure), **pending** (the
system is uncertain), or **retired** (the fact is no longer current).

| Operation | What it means |
|---|---|
| **Promote** | Move a pending fact to confident — "yes, this is actually true" |
| **Retire** | Mark a fact as outdated or superseded (requires a reason) |
| **Un-retire** | Bring a retired fact back — "actually, this is relevant again" |

Retired facts aren't deleted. They stay in the database so you can search
historical knowledge and see how things changed over time. If you
accidentally retire something, you can always un-retire it.

---

## Trust scores — the system's confidence in what it knows

Every fact in your knowledge base has a **trust score** — a number from 0.0
to 1.0 that represents how much the system trusts that fact.

### How trust starts

When the AI first extracts a fact from a document, it starts at **0.5** —
neutral. The system doesn't assume it's right or wrong. It's a starting
point.

### How trust changes

Your corrections move the score up or down:

| What you do | Trust change | After |
|---|---|---|
| New fact (AI extraction) | Starts at 0.5 | 0.50 |
| You **confirm** it | +0.05 | 0.55 |
| You confirm it again | +0.05 | 0.60 |
| You **reject** it | −0.10 | 0.40 |
| You **revise** it (creates new fact) | Resets to 0.60 | 0.60 |

A few things to notice:

- **Confirmation is gradual.** One confirmation bumps trust by 0.05. It takes
  multiple confirmations to build high trust — this prevents one mistaken
  click from locking in a wrong fact.
- **Rejection is sharper.** A single rejection drops trust by 0.10 — double
  the confirm bump — because rejecting something the AI thought was true is a
  stronger signal.
- **Revision resets to a middle ground.** When you revise, the old fact is
  retired and a new one is created at 0.60 — slightly above neutral, because
  you took the time to provide the correct version.
- **All numbers are configurable.** The exact deltas (0.05, 0.10, 0.60,
  0.50) are set in a configuration file, not hardcoded. They can be tuned as
  the system matures.

### What trust gates

Trust scores control two important behaviors:

1. **Context filtering.** Facts with trust below **0.30** are excluded from
   the AI's context when you start a conversation. If a fact has been
   repeatedly rejected and its trust has dropped below 0.30, the AI stops
   mentioning it unless you specifically search for it.

2. **The overwrite guard.** Facts with trust above **0.50** are protected
   from being silently overwritten by the AI. See the next section.

---

## The overwrite guard — your corrections stick

The overwrite guard is the rule that makes self-learning meaningful: **once
you've confirmed a fact, the AI cannot silently replace it.**

Here's how it works:

1. The AI extracts a fact from a new document — let's say "Project X deadline
   is July 15"
2. The system checks: does this conflict with an existing fact about Project
   X's deadline?
3. If the existing fact has **low trust** (≤ 0.50) — the AI updates it.
   You haven't weighed in yet, so the new information wins.
4. If the existing fact has **high trust** (> 0.50) — the AI is **blocked**
   from overwriting it. Instead, it creates a separate **pending** entry with
   the new information and flags it for your review.

The result: you see both facts side by side. The original (which you
confirmed) stays confident. The new conflicting one sits in pending until you
decide what to do:

- If the new information is correct → you can retire the old fact and confirm the new one
- If the new information is wrong → you can reject it, and the original stands
- If both are partially right → you can revise

This means the system **never silently changes something you've verified.**
It surfaces conflicts instead of resolving them behind your back.

---

## Comments — adding your context

Beyond correcting facts, you can add **comments** to any knowledge entry.
Comments are free-text notes that give the AI additional context — nuance
that doesn't fit into a fact, tag, or status change.

For example:
- "This deadline was set before the reorg — it might shift."
- "Anthony's role changed from Product Lead to Engineering Lead in Q3."
- "This policy has an exception for the APAC team — see the regional addendum."

Comments are visible to the AI during future knowledge extraction, so it can
take your context into account when processing new documents. They're
additive — you can add as many as you want, and they accumulate over time.

---

## Reports — how healthy is your knowledge base?

The system can generate on-demand reports that give you a bird's-eye view of
your knowledge base. There are five report types:

| Report | What it tells you |
|---|---|
| **Correction summary** | How many corrections have been made, by type. Are corrections trending down over time? (If yes, the system is getting more accurate.) |
| **Knowledge health** | How many facts exist per dimension. How many are confident vs pending vs retired. Which areas have thin coverage. |
| **Volatile entries** | Which specific facts have been corrected multiple times — these might need human attention or represent genuinely fluid information. |
| **Coverage gaps** | Which documents produced zero knowledge entries (the AI found nothing extractable). Which dimensions are underrepresented. Which entities appear in documents but are missing from the knowledge base. |
| **Conflicts** | Pairs of facts that contradict each other — same entity, same dimension, same tag, but different values. These are facts the overwrite guard protected, waiting for your review. |

Each report is generated by an AI that reads the raw data from your knowledge
base and writes a structured analysis. Reports are saved so you can track
trends over time.

---

## How the system uses what it learned

Your corrections feed back into the system in three ways:

### 1. Volatility flagging

When you search or browse, facts that have been corrected **3 or more times**
are marked with a `[frequently corrected]` flag. This tells both you and the
AI: "this fact has been unstable — take it with appropriate caution."

### 2. Trust-based filtering

When the AI builds context at the start of a conversation, it only includes
facts with trust above **0.30**. Low-trust facts are still in the database
and still searchable — they're just not injected into the AI's default
knowledge context. This keeps the AI's working memory clean of unreliable
information.

### 3. Few-shot learning

Confirmed corrections are recorded and can be fed back as **examples** when
the AI extracts knowledge from new documents. This is the "learning" part of
self-learning: the AI sees "here are 5 facts the user confirmed, and here are
3 they rejected" and adjusts its extraction behavior accordingly. This
improves accuracy on future documents without anyone having to rewrite the
AI's instructions.

---

## Key things to remember

- **Every correction teaches the system.** Confirm, reject, or revise — each
  one moves trust scores and improves future accuracy.
- **Trust is gradual.** One confirmation moves the needle a little (0.05).
  Trust builds over time as you validate facts. No single click locks
  anything in permanently.
- **Your verified facts are protected.** Once a fact's trust crosses 0.50,
  the AI cannot overwrite it. Conflicting new information creates a pending
  entry for your review instead.
- **Rejection is a stronger signal than confirmation.** A reject drops trust
  by 0.10 (vs +0.05 for confirm) because telling the AI "you were wrong" is
  more informative than "you were right."
- **Retired facts aren't deleted.** They stay in the database for history,
  search, and trend analysis. You can always un-retire something.
- **Comments add nuance.** Use them for context that doesn't fit into a fact
  correction — background, exceptions, relationships.
- **Reports show the big picture.** Run a health report to see how your
  knowledge base is doing: coverage gaps, volatile facts, conflicts, and
  correction trends.
- **The system improves over time.** Fewer corrections over time = the AI is
  getting better at extracting knowledge that matches your standards.
