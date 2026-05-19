---
description: Deep-research a topic or subsystem and write findings to docs/research/<topic>.md. Never writes code.
allowed-tools: Read, Grep, Glob, Bash(find:*), Bash(cat:*), Bash(grep:*)
argument-hint: <topic> [path/to/relevant/folder]
---

# Research Task

> The global HITL behavioral contract in CLAUDE.md applies in full.
> The specific rules below govern the planning workflow only.

Your ONLY job in this session is to understand. Do NOT write or modify any code.

## Input

Topic to research: $ARGUMENTS

Parse the argument: the first word is the topic slug (used for the filename). Everything after is an optional path hint pointing to relevant source files or folders.

## Step 0 — Load project context

Before doing anything else, read these files in order. They tell you where the project is, where it's going, and what code already exists. Without them, you cannot know what is relevant to the topic.

1. **`CLAUDE.md`** — Read in full. Current build progress, decisions already made, conventions in use. Tells you what is already done so you don't re-research it.

2. **`docs/roadmap.md`** — Read in full. The full phase roadmap. Tells you what the topic needs to support downstream — what phases will depend on what you're researching.

3. **`repomix-output.xml`** — Do NOT read this file top to bottom. It is large. Instead, grep surgically:
   - Use the topic name and related keywords to find relevant file blocks: `grep -n "<file path" repomix-output.xml | grep -i <topic>`
   - Extract and read those file blocks in full
   - Check what those files import, then pull those dependency blocks too
   - Repeat until you have the full dependency surface for this topic
   - Stop when you've read everything relevant — do not read unrelated files

4. **`repomix-reference.xml`** — Same surgical approach as above. Grep for the equivalent subsystem in the reference project, read those blocks in full, ignore the rest. Use this to understand proven patterns to align with or learn from.

5. **`STATE.md`** — If it exists, read it in full. Pay attention to:
   - "Architecture Decisions" — do not re-research what is already decided
   - "Cross-Phase Constraints" — your findings must flag any conflict with these
   - "Open Questions" — if any are relevant to this topic, address them in your findings

After reading, you should be able to answer:
- What already exists in the codebase that is relevant to this topic?
- What interfaces will this topic's code need to conform to?
- What does the reference project do for this subsystem that should be adopted or adapted?
- What downstream phases will depend on what gets built here?

Only then move to Step 1.

## Step 1 — Assess scope

Before diving into code, assess whether the topic is well-scoped for a
single research pass.

Ask: "Can I describe this topic's boundary in one sentence — what it
covers and what it does not?" If you cannot, the topic is too broad.

Signs a topic needs decomposition:
- It spans multiple independent subsystems (e.g., "storage-layer" covers
  connection management, migrations, query helpers, and audit logging)
- The relevant files in `repomix-output.xml` touch 4+ unrelated modules
- The roadmap shows this topic feeding into 3+ downstream phases with
  different concerns

If the topic is too broad, do NOT research all of it in one pass. Instead,
output a decomposition:

```
⚠️ Topic "$TOPIC" is too broad for a single research pass.

Recommended sub-topics:
1. <sub-topic-1> — [one sentence: what it covers]
2. <sub-topic-2> — [one sentence: what it covers]
3. <sub-topic-3> — [one sentence: what it covers]

Suggested order: [which to research first and why]

Run /research <sub-topic> to begin.
```

Then STOP. Do not proceed. Let the human confirm or adjust the decomposition.

If the topic is well-scoped, continue.

## Step 2 — Check for existing research

Check whether `docs/research/$TOPIC.md` already exists.

- **If it exists**: Read the full file carefully. You will APPEND new findings at the bottom under a new dated section. Do NOT rewrite or restructure what is already there. Skip anything already covered.
- **If it does not exist**: Create a new file from scratch.

Also check for adjacent research files that might overlap:

```bash
ls docs/research/
```

If any existing file covers a closely related topic, read its overview
and open questions. Your findings should not contradict them without
explicitly noting the conflict. If another file already answers part of
your topic, reference it instead of duplicating.

## Step 3 — Locate the relevant code

Search the project for files related to the topic. Use the optional path hint if provided. Cast a wide net:
- Grep for relevant class names, function names, module names
- Find config files, schema files, prompt files that touch this subsystem
- Trace any imports to understand the full dependency surface

Do this **in great depth and detail**. Understand the intricacies of the code, not just its surface shape. Look for edge cases, silent failure modes, and non-obvious dependencies. Do not stop until you have a complete picture.

### Calibrate depth per component

Not everything deserves the same level of investigation. For each file or
component you find, decide:

- **Deep trace** — This is a core component of the topic. Read every line,
  trace its callers and callees, understand its failure modes. Apply when:
  the component IS the topic, or downstream phases depend directly on its
  interface.

- **Surface catalog** — This touches the topic but is not central. Note
  what it does, what interface it exposes, and move on. Apply when: the
  component is a utility, a tangential dependency, or already well-understood
  from existing research.

- **Skip** — This appeared in grep results but is not actually relevant.
  Note why you're skipping it (so you don't re-investigate next time) and
  move on.

Do not spend equal time on everything. Spend time where it matters.

## Research anti-patterns — avoid these

### "I already know this"
Never assume training data is accurate for this codebase. Verify every
claim against the actual code. The reference project may have diverged.
The codebase may use a pattern differently than the standard. Grep first,
claim second.

### "The reference project does X, so we should too"
The reference project is a source of patterns, not a spec. Before
adopting a pattern, understand WHY it exists in the reference project.
If the reason doesn't apply here, don't adopt it — note why in your
findings. Cargo-culting is not research.

### "This file looks irrelevant"
Don't filter by filename alone. A file called `utils.py` might contain
the core retry logic the topic depends on. A file called `models.py`
might define the schema the topic needs to conform to. If grep found it,
read enough to confirm it's actually irrelevant before skipping.

### "I'll note this as an open question and move on"
Open questions are for things you genuinely cannot determine from the
code alone — design decisions that require human judgment, ambiguous specs,
or external dependencies you can't inspect. They are not a dump for things
you didn't investigate thoroughly. Before marking something as an open
question, confirm you've exhausted what the codebase can tell you.

## Step 4 — Write findings to file

Write your findings to `docs/research/$TOPIC.md`.

If the file is new, use this structure:

```
# Research: <Topic>
_Last updated: <date>_

## Overview
[What this subsystem does in one paragraph]

## Key Components
[Files, classes, functions — with their roles]

## How It Works
[The actual flow, with specifics. Not vague — be precise.]

## Edge Cases & Silent Failure Modes
[Things that can go wrong that are not obvious]

## Dependencies & Coupling
[What this touches, what touches it]

## Open Questions
[Things you could not determine from reading the code alone — with
a note on what you DID check before concluding it's unanswerable]

## Reference Project Patterns
[What the reference project does for this subsystem. For each pattern:
what it is, WHY it exists in the reference project, and whether the
reason applies here. Adopt, adapt, or skip — with reasoning.]

## Technical Debt Spotted
[Anything worth knowing for future work]
```

If the file already exists, append at the bottom:

```
---
## Update — <date>
[New findings only. Do not repeat what's already documented above.]
```

## Step 5 — Self-review

Before confirming, review your own findings with fresh eyes. Check for:

1. **Unsupported claims**: Did you state how something works without
   showing the grep or file that proves it? Go verify or remove the claim.
2. **Gaps disguised as confidence**: Did you gloss over a component with
   vague language ("this probably handles...") instead of admitting you
   didn't trace it? Either trace it now or move it to Open Questions with
   a note on what you checked.
3. **Missing downstream impact**: Does the roadmap show phases that depend
   on this topic? Did your findings address what those phases will need?
   If not, add a section.
4. **Contradictions with existing research**: If adjacent research files
   exist, do your findings conflict with them? Flag any contradictions
   explicitly.
5. **Cargo-culted patterns**: Did you recommend adopting a reference
   project pattern without explaining why it applies here? Add the
   reasoning or remove the recommendation.

Fix any issues inline. Do not skip this step.

## Step 6 — Confirm

After writing the file, output exactly:

```
✅ Research complete → docs/research/$TOPIC.md
Open questions remaining: <count>
Ready for: /plan $TOPIC
```

Do NOT proceed to planning or implementation. Your job ends here.
