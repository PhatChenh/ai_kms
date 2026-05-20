---
description: Scan the current conversation and extract all conceptual Q&A exchanges into a structured discussion log. Works after any session type — research, planning, implementation, or freeform discussion.
allowed-tools: Read, Edit, Bash(find:*), Bash(cat:*)
argument-hint: <topic-slug> [docs/discussions/custom-path.md]
---

# Capture Discussion Log

Your ONLY job is to extract, structure, and save conceptual exchanges from
this conversation. Do not write code. Do not continue any task.

## Input

Arguments: $ARGUMENTS

Parse the arguments:
- **First word**: the topic slug — used as the filename and section label.
  Example: `storage-layer`, `plan-review`, `async-mental-model`
- **Second word (optional)**: a custom output path to override the default.
  Example: `docs/discussions/custom.md`

If no argument is provided at all, infer the topic slug from the
conversation: use the dominant subject discussed, lowercased and hyphenated.
State your inferred slug explicitly before proceeding.

---

## Step 1 — Detect session context

Before scanning, identify what kind of session this conversation was.
The context determines the section header format in the output.

Read the opening messages of the conversation to determine:

| Session type | Signal | Section header format |
|---|---|---|
| Implementation | Plan file loaded, TDD cycle discussed, pytest output | `## Phase <N> — <Phase name>` |
| Planning | Plan file being written, architecture diagrams, phases | `## Planning Session — <feature>` |
| Research | Codebase being read, findings being written, repomix grep | `## Research Session — <topic>` |
| Freeform | No project files loaded, conceptual/learning discussion | `## Discussion — <topic>` |

State the detected session type and the section header you will use
before scanning the conversation.

If the session type is ambiguous, use `## Discussion — <topic>`.

---

## Step 2 — Check for an existing discussion file

Check whether the output file already exists.

Default output path: `docs/discussions/<topic-slug>.md`
Override path: second argument if provided.

```bash
find docs/discussions/ -name "*.md" 2>/dev/null | head -20
```

- **If the file exists**: read it in full. You will APPEND a new dated
  section at the bottom. Do not rewrite or restructure what is already there.
  Note how many sections already exist so the new one is clearly additive.
- **If it does not exist**: you will create it fresh.

---

## Step 3 — Scan the conversation

Read back through the entire conversation from the first message to now.

Your job is to find every exchange where:
- The human asked a question (explicitly or implicitly)
- You gave a conceptual explanation — something that transferred understanding

**Include:**
- Questions about how something works ("why does X behave like Y?")
- Requests to explain a concept, pattern, or decision
- Moments where the human expressed confusion and you clarified
- Design rationale discussions ("why this approach, not that one?")
- Terminology clarifications ("what does X mean in this context?")
- Mental model corrections ("I thought X worked like Y, but...")
- Any exchange where the human now understands something they didn't before

**Exclude:**
- Purely procedural exchanges ("run this command", "the test passed", "here's the file")
- Confirmations with no knowledge transfer ("yes, that's correct", "looks good")
- Status updates ("I've updated the plan", "phase 2 is done")
- Back-and-forth on phrasing or wording with no conceptual content

When uncertain whether an exchange is conceptual or procedural, include it.
It is better to capture something minor than to miss something the human will want as a flashcard.

Do not filter based on topic — capture all conceptual exchanges regardless
of whether they seem directly related to the main task. Tangential learning
is still learning.

---

## Step 4 — Structure the exchanges

Format each exchange you found exactly as follows:

```
**Q:** <question as a clean, standalone sentence>
```python
# optional: the code the human pointed to, if any
```

**A:** <explanation, starting directly — no preamble>
```python
# optional: minimum snippet illustrating the concept (3–10 lines)
```
_Key concept: <label>_
```

### Rules for Q formatting
- Paraphrase into a clean, standalone sentence
- Remove conversational openers: "wait, so...", "I don't get why...", "hmm so..."
- The question should make sense to someone reading the discussion file cold,
  without context from the rest of the conversation
- If the human asked multiple related questions in one message, split them
  into separate Q/A pairs

### Rules for A formatting
- Start directly with the explanation — cut all throat-clearing
- Remove: "Great question!", "You're right to wonder about this", "Exactly —"
- The answer should be complete but not padded
- If the explanation spans multiple concepts, split into separate Q/A pairs
  rather than writing one long answer

### Rules for code snippets
Attach a snippet when:
- The exchange was triggered by a specific piece of code the human pointed to
- A minimum runnable example would make the explanation significantly clearer
- The concept is more precisely expressed in code than in words

Do NOT attach a snippet when:
- The exchange was pure theory with no code anchor
- The snippet would require 20+ lines of context to make sense
- The concept is already precise in words alone

When attaching: trim aggressively. Remove every line that isn't load-bearing
for the concept being explained. Target 3–10 lines. Label it clearly with a
comment if the purpose isn't obvious.

### Rules for Key concept label
The `_Key concept:_` tag is required on every exchange. It is how the
flashcard command identifies what type of card to generate and how to frame
the question.

Write it as a short, precise noun phrase:
- Good: `_Key concept: Python async context manager protocol_`
- Good: `_Key concept: TDD red-green-refactor discipline_`
- Good: `_Key concept: AskUserQuestion as a hard gate vs advisory_`
- Bad: `_Key concept: coding_` (too vague)
- Bad: `_Key concept: we discussed how Claude handles decisions_` (too narrative)

---

## Step 5 — Preview and confirm before writing

Do NOT write to the file yet.

First, output a preview:

```
📋 Discussion capture preview — <topic-slug>

Session type: <detected type>
Output: <file path> (<new file / appending to existing>)
Exchanges found: <count>

---
[full formatted output of all exchanges]
---
```

Then call `AskUserQuestion` with:
- A) Write this to `<path>` — looks complete (Recommended)
- B) I want to add/remove some exchanges — describe which ones
- C) Change the output path — I'll specify
- D) The session type label is wrong — I'll correct it

Wait for the human's selection before writing anything.

---

## Step 6 — Write the file

After the human selects option A (or corrects and reconfirms):

**If creating a new file**, write with this header:

```markdown
# Discussion Log — <Topic>
_Started: <date>_

---

## <Section header from Step 1>
_Captured: <date>_

### Exchanges

[exchanges here]
```

**If appending to an existing file**, add at the bottom:

```markdown
---

## <Section header from Step 1>
_Captured: <date>_

### Exchanges

[exchanges here]
```

Do not modify anything already in the file. Append only.

---

## Step 7 — Confirm

Output exactly:

```
✅ Discussion log saved → <path>

Exchanges captured: <count>
Key concepts: <comma-separated list of all Key concept labels>

To generate flashcards from this log:
  /flashcard <topic-slug>
```

Then STOP.
