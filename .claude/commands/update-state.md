---
description: Create or update STATE.md from the current topic's plan, research, and resolved discussions. Run at end of session or when a milestone is reached.
allowed-tools: Read, Edit, Bash(find:*), Bash(cat:*), Bash(grep:*)
argument-hint: <topic>
---

# Update State Task

Your ONLY job is to update STATE.md accurately. Do NOT write or modify any code.

## Input

Topic to update state for: $ARGUMENTS

## Step 1 — Determine mode

Check whether `STATE.md` exists at the project root.

---

### Path A — STATE.md does NOT exist (initialization)

Read these in order before writing anything:

1. **`CLAUDE.md`** — full read. Current build progress, conventions, rules already established.
2. **`docs/roadmap.md`** — full read. All phases and what each must deliver. You need this to assess the downstream impact of every decision you record.
3. **`repomix-output.xml`** — surgical grep only. For each item marked done in CLAUDE.md's checklist, grep for the files it touches and read those blocks. Understand what was actually built.
4. **`docs/research/$TOPIC.md`** — if it exists, read in full.
5. **`docs/plans/$TOPIC.md`** — if it exists, read in full per Step 2 below.

Then go to Step 3.

---

### Path B — STATE.md exists (session update)

Read these in order:

1. **`STATE.md`** — full read. This is your baseline. Do not re-derive or re-record what is already here.
2. **`CLAUDE.md`** — full read. Check if current position has changed.
3. **`docs/plans/$TOPIC.md`** — if it exists, read in full per Step 2 below.

Then go to Step 3.

---

## Step 2 — Extract from the plan file

When reading `docs/plans/$TOPIC.md`, extract from these specific locations:

**`# RESOLVED:` annotations** — these are inline records of decisions made during planning discussions. Each one contains: what was decided, what alternatives were considered, and why this choice was made. Read every `# RESOLVED:` block in full. Each one is a candidate Architecture Decision for STATE.md. Ask for each: does this choice constrain how future phases must be built? If yes, record it.

**`## Open Questions`** — these are cross-phase questions explicitly left unresolved. Copy them into STATE.md Open Questions as-is. Note which phase they block based on the context in the question.

**`## Out of Scope`** — these are explicitly deferred items. Read each one and classify it:
- If it is deferred to a specific named phase → Technical Debt row (owned by that phase)
- If it is deferred indefinitely or conditionally → Open Question or a note under the relevant Architecture Decision
- If it is a hard boundary ("the vault is the source of truth", "documents does not store body content") → Cross-Phase Constraint

**`## Approach`** — the opening paragraph often contains key design choices stated plainly (e.g. "raw sqlite3 over ORM", "versioned .sql deltas"). These are Architecture Decisions if they constrain future phases.

## Step 3 — Write STATE.md

**If initializing (Path A)**, create `STATE.md` at the project root:

~~~
# STATE.md — Cross-Session Project State
_Created: <date>_
_Last updated: <date>_

## Current Position
**Phase**: <current phase name and number from CLAUDE.md>
**Checklist**:
- [x] <completed item>
- [ ] <pending item>
...

## Architecture Decisions

### [DECISION-001] <Short title>
- **Source**: `docs/plans/<topic>.md` — `# RESOLVED` in Phase <N>
- **Decision**: <what was chosen, one sentence>
- **Alternatives considered**: <what else was evaluated>
- **Rationale**: <why this choice>
- **Constraint for future phases**: <what future phases must do or must never do as a result>

### [DECISION-002] ...

## Technical Debt
| ID | What | Why deferred | Owned by phase | Source |
|---|---|---|---|---|
| TD-001 | <what> | <why acceptable now> | Phase <N> | Out of Scope, `plans/<topic>.md` |

## Cross-Phase Constraints
- <rule that every future phase must honor, no exceptions>

## Open Questions
| ID | Question | Blocks | Status |
|---|---|---|---|
| Q-001 | <question> | Phase <N> | 🔴 Open |
~~~

**If updating (Path B)**, make only additive changes:
- New decision → append to "Architecture Decisions" with next ID
- New debt → append row to "Technical Debt"
- New constraint → append bullet to "Cross-Phase Constraints"
- Resolved question → change status to ✅ Resolved, add one-line answer inline
- New question → append row to "Open Questions"
- Position change → update "Current Position" in place

Never delete existing entries. If superseded, add inline: `_(superseded by DECISION-NNN)_`

Always update `_Last updated:` at the top.

## Step 4 — Confirm

Output exactly:

~~~
✅ STATE.md <initialized | updated> — <date>

Changes made:
- [one line per entry added or updated, with its source location in the plan]

Skipped:
- [anything reviewed that did not warrant a STATE.md entry, and why]
~~~

Then stop.