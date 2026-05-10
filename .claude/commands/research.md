---
description: Deep-research a topic or subsystem and write findings to docs/research/<topic>.md. Never writes code.
allowed-tools: Read, Grep, Glob, Bash(find:*), Bash(cat:*), Bash(grep:*)
argument-hint: <topic> [path/to/relevant/folder]
---

# Research Task

Your ONLY job in this session is to understand. Do NOT write or modify any code.

## Input

Topic to research: $ARGUMENTS

Parse the argument: the first word is the topic slug (used for the filename). Everything after is an optional path hint pointing to relevant source files or folders.

## Step 0 — Load project context

Before doing anything else, read these four files in order. They tell you where the project is, where it's going, and what code already exists. Without them, you cannot know what is relevant to the topic.

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

## Step 1 — Check for existing research

Check whether `docs/research/$TOPIC.md` already exists.

- **If it exists**: Read the full file carefully. You will APPEND new findings at the bottom under a new dated section. Do NOT rewrite or restructure what is already there. Skip anything already covered.
- **If it does not exist**: Create a new file from scratch.

## Step 2 — Locate the relevant code

Search the project for files related to the topic. Use the optional path hint if provided. Cast a wide net:
- Grep for relevant class names, function names, module names
- Find config files, schema files, prompt files that touch this subsystem
- Trace any imports to understand the full dependency surface

Do this **in great depth and detail**. Understand the intricacies of the code, not just its surface shape. Look for edge cases, silent failure modes, and non-obvious dependencies. Do not stop until you have a complete picture.

## Step 3 — Write findings to file

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
[Things you could not determine from reading the code alone]

## Reference Project Patterns
[What the reference project does for this subsystem. What to adopt, what to adapt, what to skip and why.]

## Technical Debt Spotted
[Anything worth knowing for future work]
```

If the file already exists, append at the bottom:

```
---
## Update — <date>
[New findings only. Do not repeat what's already documented above.]
```

## Step 4 — Confirm

After writing the file, output exactly:

```
✅ Research complete → docs/research/$TOPIC.md
Open questions remaining: <count>
Ready for: /plan $TOPIC
```

Do NOT proceed to planning or implementation. Your job ends here.
