# Plan: Classification Prompt YAML
_Last updated: 2026-06-08_
_Status: [ ] pending_

**Mini-spec:** [docs/2_specs/classify-prompt-mini.md](../2_specs/classify-prompt-mini.md)
**Behavior IDs covered:** P2-CPROMPT-01 through P2-CPROMPT-04

---

## Architecture

### Q1 — What happens inside (what the AI does when it receives this prompt)

```
# Classification Prompt — What Happens Inside
Scope: Shows what the AI does when it receives this prompt for one note.
       Does NOT cover how the pipeline calls it or what happens after.

How to read this:
  Boxes  = steps the AI takes in order
  Arrows = what happens next
  Forks  = a decision with two outcomes

          Note arrives at the prompt
                     │
                     ▼
          ┌─────────────────────┐
          │ Read system rules   │
          │ Role, output format,│
          │ and routing rules   │
          └──────────┬──────────┘
                     │
                     ▼
          ┌─────────────────────┐
          │ Learn: project vs   │
          │ domain routing,     │
          │ prefer specific     │
          │ project over domain │
          └──────────┬──────────┘
                     │
                     ▼
          ┌─────────────────────┐
          │ Learn: Uncategorized│
          │ rule — never return │
          │ "Uncategorized" as  │
          │ the destination     │
          └──────────┬──────────┘
                     │
                     ▼
          ┌─────────────────────┐
          │ Read note data:     │
          │ title, summary,     │
          │ tags, valid         │
          │ destinations list   │
          └──────────┬──────────┘
                     │
                     ▼
          ┌─────────────────────┐
          │ Find best match     │
          │ from destinations   │
          │ list                │
          └──────────┬──────────┘
                     │
              ┌──────┴──────┐
              │             │
          CERTAIN        UNSURE
              │             │
              ▼             ▼
       ┌──────────┐   ┌──────────┐
       │ Return   │   │ Return   │
       │ answer   │   │ answer   │
       │ high     │   │ low      │
       │ confidence│  │confidence│
       └──────────┘   └──────────┘

Both outcomes: JSON with target_type, target_name,
               confidence, and reasoning — nothing else.
```

---

### Q2 — How it connects (from spec)

```
# Classification Prompt — How It Connects
Scope: Shows what data feeds into the Classification Prompt and what uses its output.
       Does NOT show the prompt's internal steps (see Q1 for that).

How to read this:
  Center box    = the feature being built (the prompt file)
  Solid boxes   = components already built or being built alongside
  Arrow labels  = what passes between them

                        ┌──────────────────────┐
                        │  Project Registry    │
                        │  Knows all valid     │
                        │  destinations        │
                        └──────────┬───────────┘
                                   │ destinations list
                                   │ (domains + projects,
                                   │  Uncategorized last)
                                   ▼
  ┌───────────────────┐   ┌─────────────────────┐   ┌──────────────────┐
  │  Note Metadata    │   │                     │   │  Classify        │
  │  Title, summary,  ├──►│  CLASSIFICATION     ├──►│  Pipeline        │
  │  and tags set by  │   │  PROMPT             │   │  Fills in the    │
  │  Phase 1 capture  │   │                     │   │  prompt and      │
  └───────────────────┘   └─────────────────────┘   │  calls the AI    │
   title, summary, tags                             └────────┬─────────┘
                                                             │
                                                      AI answer in JSON:
                                                      type, name,
                                                      confidence, reasoning
                                                             │
                                                   ┌─────────┴──────────┐
                                                   ▼                    ▼
                                          ┌───────────────┐  ┌──────────────────┐
                                          │  Confidence   │  │  Decision Log    │
                                          │  Gate         │  │  Records where   │
                                          │  Decides:     │  │  it went, how    │
                                          │  auto-move,   │  │  confident, and  │
                                          │  flag, or     │  │  why — for audit │
                                          │  leave inbox  │  └──────────────────┘
                                          └───────────────┘
```

---

### Q3 — Why build it this way

```
# Classification Prompt — Why This Way
Scope: Rules and existing patterns this prompt design must conform to.
       Does NOT cover internal steps (Q1) or connections (Q2).

How to read this:
  Center box        = the feature being built
  Surrounding boxes = rules it must follow
  Lines             = which rule applies where

  ┌──────────────────────────┐       ┌──────────────────────────┐
  │ Prompts live in YAML     │       │ JSON-only output pattern │
  │ files, never as strings  │       │ matches the folder       │
  │ inside code — enforced   │       │ classifier already in    │
  │ by project rules         │       │ the codebase             │
  └────────────┬─────────────┘       └──────────────┬───────────┘
               │                                    │
               │         ┌──────────────────┐       │
               └────────►│  CLASSIFICATION  │◄──────┘
                         │  PROMPT          │
               ┌────────►│  classify.yaml   │◄──────┐
               │         └──────────────────┘       │
               │                                    │
  ┌────────────┴─────────────┐       ┌──────────────┴───────────┐
  │ Four-field output        │       │ Confidence scale from     │
  │ target_type + target_name│       │ 0.0 to 1.0 — same        │
  │ + confidence + reasoning │       │ guidance as folder        │
  │ (reasoning is new vs     │       │ classifier: 0.9+ certain, │
  │  folder classifier)      │       │ below 0.7 uncertain       │
  └──────────────────────────┘       └───────────────────────────┘
```

---

## Approach

Write one YAML file. No code changes. The Classify pipeline (built later) will load
this file via the existing Prompt Loader and pass the four variables at runtime.

---

## Build step

### Step 1 — Write `src/prompts/classify.yaml`

Create the file with this exact content:

```yaml
name: classify
system: |
  You are a knowledge management assistant. Your job is to classify an inbox note
  into the most appropriate destination folder in the vault.

  Respond with valid JSON only — no markdown, no explanation. The JSON must have exactly
  these four fields:
    "target_type": one of "domain" or "project"
    "target_name": the exact name of the destination (never "Uncategorized")
    "confidence": a float between 0.0 and 1.0
    "reasoning": one sentence explaining your choice

  Routing rules:
  - Use "project" when the note is tied to active work for a specific engagement
    (meeting notes, deliverables, decisions, status updates).
  - Use "domain" when the note is general or durable knowledge for a business area
    (reference material, research, industry context, background reading).
  - When both a project and its parent domain could fit, always prefer the specific project.

  Reading the destinations list:
  - Destinations are grouped by domain. The domain name is the group header.
  - Under each domain, the listed projects are valid "project" destinations.
  - The domain name itself is also a valid "domain" destination.
  - The Uncategorized group lists projects with no domain assignment yet.
    These are still valid project destinations — use the project name as target_name.
    Never return target_name: "Uncategorized".

  Confidence guidance:
  - 0.9 or higher: you are very certain about the destination.
  - 0.7 to 0.9: likely correct, but some ambiguity.
  - Below 0.7: uncertain — prefer a lower score over forcing a confident answer.

user: |
  Classify the following inbox note:

  Title: {{ title }}

  Summary: {{ summary }}

  Tags: {{ tags }}

  Available destinations:
  {{ valid_destinations }}

  Respond with JSON only.
variables: [title, summary, tags, valid_destinations]
```

**Files to create:**
- `src/prompts/classify.yaml` — **New file**

---

## Verification

After writing the file:

1. Read it back and confirm the four variables appear in the `user:` section as
   `{{ title }}`, `{{ summary }}`, `{{ tags }}`, `{{ valid_destinations }}`.
2. Confirm `variables:` list matches exactly: `[title, summary, tags, valid_destinations]`.
3. Confirm `name: classify` matches the filename.
4. Run `uv run pytest tests/ -m "not smoke"` — all existing tests must stay green
   (this file adds no code, so no test breakage is expected).

---

## Success criteria

- [ ] P2-CPROMPT-01: When called, AI returns JSON with target_type, target_name,
      confidence, reasoning — no prose, no markdown fences
- [ ] P2-CPROMPT-02: Active-work notes route to `target_type=project`; general-knowledge
      notes route to `target_type=domain`
- [ ] P2-CPROMPT-03: AI never returns `target_name="Uncategorized"` — routes to the
      actual project name within that group
- [ ] P2-CPROMPT-04: When no destination fits well, AI returns low confidence
      (`< 0.7`) rather than forcing a high-confidence wrong answer

Note: P2-CPROMPT-01 through P2-CPROMPT-04 require a live LLM call to test
(tier: smoke). They are verified when the Classify pipeline component is built.

---

## Out of scope

- Classify pipeline code (`pipelines/classify.py`) — separate plan
- Confidence Gate, Route, Move, Decision Log — separate plans
- Project Registry (`vault/registry.py`) — separate plan already exists
