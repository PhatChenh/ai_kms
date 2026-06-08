# Mini-Spec: Classification Prompt YAML
_From grill interview, 2026-06-08_

**Behavior IDs:** P2-CPROMPT-01 through P2-CPROMPT-04

## Requirement (restated)
Create `src/prompts/classify.yaml` — a YAML prompt file that tells the AI how to
classify a single inbox note into a destination project or domain folder, using the
note's title, summary, tags, and a pre-formatted list of valid destinations from the
Project Registry.

## Scope
- **In:** `src/prompts/classify.yaml` — one new file only
- **Out:** Classify pipeline code, Confidence Gate, Route, Move, Decision Log
  (those are separate components built later)

## Done when
1. `src/prompts/classify.yaml` exists and follows the same YAML structure as
   `src/prompts/classify_folder.yaml` — keys: `name`, `system`, `user`, `variables`.
2. `variables` list contains exactly: `title`, `summary`, `tags`, `valid_destinations`.
3. System prompt instructs the AI to return **JSON only** — no markdown, no explanation —
   with exactly these four fields:
   - `target_type`: `"domain"` or `"project"`
   - `target_name`: exact destination name (never `"Uncategorized"`)
   - `confidence`: float between 0.0 and 1.0
   - `reasoning`: one sentence
4. System prompt explains the routing rule: use `"project"` when the note is tied to active
   work for a specific engagement; use `"domain"` when it is general or durable knowledge.
   When both fit, prefer the specific project over the parent domain.
5. System prompt includes confidence guidance (same scale as `classify_folder.yaml`):
   0.9+ = very certain, 0.7–0.9 = likely correct, below 0.7 = uncertain — prefer a lower
   score over forcing a confident answer when unsure.
6. System prompt explains the Uncategorized group: projects listed under it are valid
   routing destinations — use the project name as `target_name`; never return
   `target_name: "Uncategorized"`.
7. User prompt has `{{ title }}`, `{{ summary }}`, `{{ tags }}`, and
   `{{ valid_destinations }}` placeholders in natural reading order.

## Edge cases discussed
- **No good match:** return best guess at low confidence; Confidence Gate handles
  `mark-clueless` for below-threshold scores.
- **Project in Uncategorized group:** route to the project name
  (`target_type: "project", target_name: "NewProject"`); never to `"Uncategorized"`.
- **Domain folder and project both fit:** prefer the specific project.
- **`valid_destinations` format:** string produced by `format_for_prompt()` from
  `vault/registry.py` — domain names as group headers, project names under them,
  Uncategorized group last with a "use semantic reasoning" note.
