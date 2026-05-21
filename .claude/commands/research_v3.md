---
description: Deep-research a topic or subsystem and write findings to docs/research/<topic>.md. Never writes code.
allowed-tools: Read, Grep, Glob, Bash(find:*), Bash(cat:*), Bash(grep:*)
argument-hint: <topic> [path/to/relevant/folder]
---

# Research Task

> The global HITL behavioral contract in CLAUDE.md applies in full.
> The specific rules below govern the research workflow only.

Your ONLY job in this session is to understand. Do NOT write or modify any code.

## Input

Topic to research: $ARGUMENTS

Parse the argument: the first word is the topic slug (used for the filename). Everything after is an optional path hint pointing to relevant source files or folders.

---

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

Only then move to Step 0b.

---

## Step 0b — Surface blocking conflicts and ambiguities  ← NEW

After loading all context, before doing any research:

Scan what you just read for conflicts and ambiguities that would affect the direction of the research. Look for:
- A STATE.md architecture decision that contradicts something in the roadmap
- An open question in STATE.md that is directly relevant to this topic and still unresolved
- CLAUDE.md conventions that conflict with what the reference project does
- Anything that makes the research scope unclear — where the topic boundary is ambiguous

For each blocking conflict or ambiguity found, call `AskUserQuestion` with:
- A one-line description of the conflict or ambiguity
- 2–3 options for how to resolve or frame it
- Your recommended interpretation labeled "(Recommended)"

Do not proceed to Step 1 until every blocking conflict is resolved.

If no blocking conflicts exist, state: "No blocking conflicts found — proceeding to scope assessment."

---

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

If the topic is too broad, do NOT research all of it in one pass. Call `AskUserQuestion` with:  ← CHANGED

```
⚠️ Topic "$TOPIC" is too broad for a single research pass.
```

Options to present:
- A) Research [sub-topic-1] first — [one sentence: what it covers] (Recommended)
- B) Research [sub-topic-2] first — [one sentence: what it covers]
- C) Research [sub-topic-3] first — [one sentence: what it covers]
- D) Adjust the decomposition — describe what you'd change

Then STOP. Do not proceed until the human selects an option.

If the topic is well-scoped, confirm the scope in one sentence and continue:
```
Scope confirmed: This research covers [X] and excludes [Y]. Proceeding.
```

---

## Step 2 — Check for existing research

Check whether `docs/research/$TOPIC.md` already exists.

- **If it exists**: Read the full file carefully. You will APPEND new findings at the bottom under a new dated section. Do NOT rewrite or restructure what is already there. Skip anything already covered.
- **If it does not exist**: Create a new file from scratch.

Also check for adjacent research files that might overlap:

```bash
ls docs/research/
```

If any existing file covers a closely related topic, read its overview and open questions.

If your planned findings directly contradict an existing research file, do NOT silently note it and move on. Call `AskUserQuestion` with:  ← CHANGED
- A description of the contradiction (what the existing file says vs what you found)
- Options: A) trust the existing finding, B) override it with new evidence (and document why), C) flag as unresolved open question
- Your recommendation labeled "(Recommended)"

Wait for the human's decision before proceeding.

---

## Step 3 — Calibration plan before diving deep 

Before starting code investigation, output a calibration plan listing every
file or component you intend to investigate, with the depth level assigned:

```
📋 Investigation plan for "$TOPIC":

Deep trace (will read every line, trace callers/callees, map failure modes):
- `path/to/file.py` — [why it's core]
- `path/to/other.py` — [why it's core]

Surface catalog (will note interface and role, not full trace):
- `path/to/util.py` — [why it's peripheral]

Skipping (appeared in grep results but not relevant):
- `path/to/irrelevant.py` — [why skipping]

Estimated scope: [X files deep trace, Y surface]
```

Then call `AskUserQuestion` with:
- A) This looks right — proceed with investigation (Recommended)
- B) [specific file] should be deep-traced, not surface — proceed adjusted
- C) [specific file] can be skipped — proceed adjusted
- D) The scope is wrong, let me redirect — [human describes correction]

Do not begin Step 4 until the human approves or adjusts the calibration plan.

This gate exists because going deep on the wrong component wastes the entire
session. One minute of calibration saves an hour of wrong-direction research.

---

## Step 4 — Locate and investigate the relevant code

Search the project for files related to the topic. Use the optional path hint if provided. Cast a wide net:
- Grep for relevant class names, function names, module names
- Find config files, schema files, prompt files that touch this subsystem
- Trace any imports to understand the full dependency surface

Follow the calibration plan approved in Step 3. Do not change depth levels
mid-investigation without flagging it.

Do this **in great depth and detail** on components marked for deep trace.
Understand the intricacies of the code, not just its surface shape. Look for
edge cases, silent failure modes, and non-obvious dependencies. Do not stop
until you have a complete picture of what you committed to investigate.

### Calibrate depth per component

Apply the depth levels from your approved calibration plan:

- **Deep trace** — Read every line, trace callers and callees, understand failure modes. Apply when: the component IS the topic, or downstream phases depend directly on its interface.

- **Surface catalog** — Note what it does, what interface it exposes, move on. Apply when: the component is a utility, tangential dependency, or already well-understood from existing research.

- **Skip** — Note why you're skipping it so you don't re-investigate next time.

If mid-investigation you discover a component that should be deep-traced but wasn't in the plan, do not silently adjust. Call `AskUserQuestion`:  ← CHANGED
- "[file] turned out to be more central than expected. Should I deep-trace it now?"
- Options: A) Yes, trace it fully, B) Surface catalog only, C) Add to a follow-up research pass

---

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

---

## Step 5 — Write findings to file

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

## Extension Points
[For each key class or function in this subsystem: is its behavior
hardcoded or injectable? List every place where a new variant could
be swapped in without touching the core pipeline. Flag any place that
would require code surgery to customize — those are coupling risks.
Format: component → how it could be extended → what blocks extension today]

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

---

## Step 6 — Self-review

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

For issues that are clear factual gaps or unsupported claims: fix inline.

For issues that require a design decision — where you found two valid
interpretations and aren't sure which the human intends — call
`AskUserQuestion` before writing the final finding.  ← CHANGED

Do not skip this step.

---

## Step 7 — Confirm

After writing the file, output exactly:

```
✅ Research complete → docs/research/$TOPIC.md
Open questions remaining: <count>
Ready for: /plan $TOPIC
```

Do NOT proceed to planning or implementation. Your job ends here.
